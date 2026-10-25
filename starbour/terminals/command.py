"""T1 Command process - T1, UX-01...08, CMD-*, D-92, D-94, D-105, D-112.

prompt_toolkit prompt in the main thread (history, tab completion of commands and ship
names); a daemon thread listens to the bus, keeps the latest snapshot and prints replies
above the prompt via ``patch_stdout``.  ``end`` locks the prompt behind a spinner until
``turn.resolved`` (UX-06) and asks first when a hazard countdown is running with no move
or warp queued (CMD-17, UX-04).  After game over only ``new`` and ``quit`` do anything
(CMD-19).  Without a console (MOS_HEADLESS=1 in tests) T1 only listens.
"""
from __future__ import annotations

import io
import logging
import sys
import threading
import time

from rich.console import Console

from starbour import runtime
from starbour.bus import messages as M
from starbour.bus.zmq_io import Pusher, Subscriber
from starbour.commands.catalogue import available_names
from starbour.commands.parse import CommandError, parse, parse_help, parse_view
from starbour.config import GameConfig
from starbour.terminals import palette as P
from starbour.terminals import render_command as RC
from starbour.terminals._base import TITLE, make_console

log = logging.getLogger("starbour.command")
REPLY_TIMEOUT_S = 5.0
RESOLVE_TIMEOUT_S = 60.0
HELLO_TIMEOUT_S = 30.0
SPINNER = "|/-\\"


class Listener(threading.Thread):
    """SUB loop: snapshot, replies, resolved flag."""

    def __init__(self, pub_port: int, console: Console) -> None:
        super().__init__(name="bus-listener", daemon=True)
        self.sub = Subscriber(pub_port)
        self.console = console
        self.lock = threading.Lock()
        self.snapshot: dict | None = None
        self.turn_no = 0
        self.first_hello = threading.Event()
        self.updated = threading.Event()     # set whenever a fresh snapshot arrives (hello or resolved)
        self.resolved = threading.Event()
        self.reply = threading.Event()
        self.last_reply: tuple[str, dict] | None = None
        self.shutdown = threading.Event()
        self.last_events: list[dict] = []
        self.halt = False

    def run(self) -> None:
        while not self.halt:
            got = self.sub.recv(250)
            if got is None:
                continue
            topic, payload, _ = got
            try:
                self.handle(topic, payload)
            except Exception:   # noqa: BLE001
                log.exception("listener error on %s", topic)

    def handle(self, topic: str, payload: dict) -> None:
        if topic in (M.STATE_HELLO, M.TURN_RESOLVED):
            snap = payload.get("snapshot")
            if isinstance(snap, dict):
                with self.lock:
                    self.snapshot = snap
                    self.turn_no = int(payload.get("turn_no", snap.get("turn_no", 0)))
                self.first_hello.set()
                self.updated.set()
            if topic == M.TURN_RESOLVED:
                self.last_events = list(payload.get("events") or [])
                self.resolved.set()
        elif topic in (M.ORDER_ACCEPTED, M.ORDER_REJECTED):
            self.last_reply = (topic, payload)
            self.reply.set()
        elif topic == M.EVENT_LOG:
            ev = M.EventLog.from_dict(payload)
            if ev.severity in ("alert", "highlight", "error"):
                self.console.print(f"  {ev.text}", style=P.severity_style(ev.severity))
        elif topic == M.SYSTEM_SHUTDOWN:
            self.shutdown.set()
        elif topic == M.GAME_OVER:
            end = M.GameEnd.from_dict(payload)   # OUT-16, CMD-19
            self.console.print("  GAME OVER", style=P.STYLES["game_over"])
            self.console.print(f"  {end.cause} - turn {end.turn_no} reached", style=P.STYLES["bad"])
            if end.summary:
                self.console.print("  run: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in end.summary.items() if k != "turns"),
                                   style=P.STYLES["value"])
            self.console.print("  Type 'new' to start again with a new seed (CMD-19).", style=P.STYLES["confirm"])
        elif topic == M.GAME_MASTERY:
            end = M.GameEnd.from_dict(payload)
            self.console.print(f"  {topic}: {end.cause}", style=P.STYLES["title"])

    def current(self) -> tuple[dict | None, int]:
        with self.lock:
            return self.snapshot, self.turn_no

    def close(self) -> None:
        self.halt = True
        self.sub.close(0)


class CommandTerminal:
    def __init__(self, cfg: GameConfig, pub_port: int, pull_port: int) -> None:
        self.cfg = cfg
        self.console = make_console()
        self.listener = Listener(pub_port, self.console)
        self.push = Pusher(pull_port)
        self.pending: dict[str, int] = {}
        self.local_orders: dict[str, list[str]] = {}

    # ---- helpers
    def active_ship_id(self) -> str:
        snap, _ = self.listener.current()
        if not snap:
            return ""
        cap = (snap.get("captains") or {}).get(snap.get("player_id")) or {}
        return str(cap.get("active_ship") or "")

    def ship_names(self) -> list[str]:
        snap, _ = self.listener.current()
        if not snap:
            return []
        cap = (snap.get("captains") or {}).get(snap.get("player_id")) or {}
        ships = snap.get("ships") or {}
        return [str(ships[s]["name"]) for s in cap.get("ships") or [] if s in ships]

    def active_ship(self) -> dict | None:
        snap, _ = self.listener.current()
        if not snap:
            return None
        return (snap.get("ships") or {}).get(self.active_ship_id())

    def game_over(self) -> bool:
        snap, _ = self.listener.current()
        return bool(snap and snap.get("game_over"))

    def countdown_without_escape(self) -> dict | None:
        """CMD-17 / UX-04: the active ship's hazard when a countdown runs and no move or warp is queued."""
        ship = self.active_ship()
        if not ship or not isinstance(ship.get("hazard"), dict):
            return None
        if any(o.get("command") in ("move", "warp") for o in ship.get("orders") or []):
            return None
        return ship["hazard"]

    def send_order(self, command: str, args: list[str]) -> tuple[str, dict] | None:
        snap, turn_no = self.listener.current()
        self.listener.reply.clear()
        self.listener.updated.clear()
        self.listener.last_reply = None
        self.push.send(M.ORDER_SUBMITTED, M.OrderSubmitted(command, list(args), self.active_ship_id(), turn_no, "command").to_dict())
        if not self.listener.reply.wait(REPLY_TIMEOUT_S):
            self.console.print("  no reply from the engine within 5 s - is it running? (logs/engine.log)", style=P.STYLES["command_error"])
            return None
        reply = self.listener.last_reply
        if reply and reply[0] == M.ORDER_ACCEPTED:
            self.listener.updated.wait(REPLY_TIMEOUT_S)   # D-91 / D-99: the engine sends a fresh hello after every accepted order
        return reply

    def show_reply(self, reply: tuple[str, dict] | None, order_text: str) -> None:
        if reply is None:
            return
        topic, payload = reply
        if topic == M.ORDER_REJECTED:
            self.console.print(f"  rejected: {payload.get('reason', '')}", style=P.STYLES["command_error"])
            return
        acc = M.OrderAccepted.from_dict(payload)
        sid = str(acc.order.get("ship_id") or self.active_ship_id())
        self.pending[sid] = acc.queued
        text = acc.text or f"queued as order {acc.queue_position} for this turn"
        self.console.print(f"  ok: {text}", style=P.STYLES["command_ok"])

    def confirm(self, session, question: str) -> bool:
        """UX-04: y/N confirmation."""
        try:
            answer = session.prompt(f"{question} [y/N] ")
        except (EOFError, KeyboardInterrupt):
            return False
        return answer.strip().lower() in ("y", "yes")

    def spinner_wait(self) -> bool:
        """UX-06: lock and spin until turn.resolved (or the timeout)."""
        t0 = time.perf_counter()
        i = 0
        while not self.listener.resolved.wait(0.1):
            if self.listener.shutdown.is_set():
                return False
            if time.perf_counter() - t0 > RESOLVE_TIMEOUT_S:
                sys.stdout.write("\r" + " " * 40 + "\r")
                self.console.print("  the engine did not resolve the turn within 60 s (logs/engine.log)", style=P.STYLES["command_error"])
                return False
            sys.stdout.write(f"\r  {SPINNER[i % len(SPINNER)]} resolving turn...")
            sys.stdout.flush()
            i += 1
        sys.stdout.write("\r" + " " * 40 + "\r")
        sys.stdout.flush()
        return True

    # ---- the loop
    def run(self) -> int:
        self.console.set_window_title(f"T1 Command - {TITLE}")
        self.listener.start()
        runtime.start_parent_watchdog(on_exit=lambda: (self.listener.close(), self.push.close(0)))
        headless = runtime.headless() or not sys.stdin.isatty()
        if headless:
            return self.run_headless()
        from prompt_toolkit import PromptSession
        from prompt_toolkit.completion import WordCompleter
        from prompt_toolkit.formatted_text import ANSI
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.patch_stdout import patch_stdout

        self.console.clear()
        self.console.print("Master of Starbour - T1 Command", style=P.STYLES["title"])
        self.console.print("connecting to the engine ...", style=P.STYLES["command_info"])
        if not self.listener.first_hello.wait(HELLO_TIMEOUT_S):
            self.console.print(f"no state.hello from the engine within {HELLO_TIMEOUT_S:.0f} s - see logs/engine.log", style=P.STYLES["command_error"])
            return 1
        snap, _ = self.listener.current()
        cap = (snap.get("captains") or {}).get(snap.get("player_id")) or {}
        self.console.print(f"Welcome, Captain {cap.get('name', '?')}. Type 'help' for the commands; 'move x y' then 'end' to play a turn.",
                           style=P.STYLES["command_info"])
        history_path = runtime.paths.logs_dir() / "command_history.txt"
        history_path.parent.mkdir(parents=True, exist_ok=True)
        session: PromptSession = PromptSession(history=FileHistory(str(history_path)))

        def completer() -> WordCompleter:
            words = available_names() + ["universe", "galaxy", "sector", "follow", "cancel", "all", "status"] + self.ship_names()
            return WordCompleter(words, ignore_case=True)

        while not self.listener.shutdown.is_set():
            snap, _ = self.listener.current()
            sid = self.active_ship_id()
            prompt_ansi = self._prompt_ansi(snap, self.pending.get(sid))
            try:
                with patch_stdout(raw=True):
                    line = session.prompt(ANSI(prompt_ansi), completer=completer(), complete_while_typing=False)
            except KeyboardInterrupt:
                continue
            except EOFError:
                line = "quit"
            if self.listener.shutdown.is_set():
                break
            if not self.handle_line(line.strip(), session):
                break
        self.listener.close()
        self.push.close(0)
        return 0

    def _prompt_ansi(self, snap: dict | None, pending: int | None) -> str:
        buf = Console(file=io.StringIO(), force_terminal=True, color_system="truecolor", width=200, highlight=False, markup=False)
        buf.print(RC.render(snap or {}, None, self.cfg, 200, pending=pending), end="")
        return buf.file.getvalue()

    def handle_line(self, line: str, session) -> bool:
        """One command line; returns False to exit."""
        if not line:
            return True
        try:
            order = parse(line, self.active_ship_id())
        except CommandError as e:
            self.console.print(f"  {e}", style=P.STYLES["command_error"])
            return True
        cmd, args = order.command, list(order.args)
        snap, _ = self.listener.current()
        snap = snap or {}
        if cmd == "help":
            for t in RC.help_text(parse_help(args)):
                self.console.print(t)
        elif cmd == "status":
            for t in RC.status_lines(snap, self.cfg):
                self.console.print(t)
        elif cmd == "fleet":
            self.console.print(RC.fleet_table(snap))
        elif cmd == "shields":
            for t in RC.shields_lines(snap):   # CMD-09: answered from the snapshot (D-92)
                self.console.print(t)
        elif cmd == "view":
            level = parse_view(args)
            self.push.send(M.VIEW_CHANGED, M.ViewChanged(level).to_dict())
            self.console.print(f"  grid view: {level}", style=P.STYLES["command_ok"])
        elif cmd == "end":
            if self.game_over():
                self.console.print("  game over - type 'new' to start again (CMD-19)", style=P.STYLES["command_error"])
                return True
            hz = self.countdown_without_escape()
            if hz is not None:
                kind = str(hz.get("kind", "hazard")).replace("_", " ")
                question = (f"A {kind} is pulling you in ({hz.get('turns_left')} turn(s) left) and no move or warp is queued. "
                            f"End the turn anyway?")
                if not self.confirm(session, question):   # CMD-17, UX-04
                    self.console.print("  turn not ended - queue a move or warp first", style=P.STYLES["command_info"])
                    return True
            self.listener.resolved.clear()
            _, turn_no = self.listener.current()
            self.push.send(M.TURN_END, M.TurnEnd(turn_no).to_dict())
            if self.spinner_wait():
                self.pending.clear()
                _, new_turn = self.listener.current()
                notable = [e for e in self.listener.last_events if e.get("severity") in ("warning", "alert", "highlight", "error")]
                self.console.print(f"  turn {new_turn} resolved - see T3 for the log", style=P.STYLES["command_ok"])
                for e in notable:
                    self.console.print(f"  {e.get('text', '')}", style=P.severity_style(str(e.get("severity"))))
        elif cmd == "quit":
            if self.confirm(session, "Quit the game? It is autosaved every turn."):
                self.push.send(M.SYSTEM_SHUTDOWN, M.SystemShutdown("quit from the command terminal").to_dict())
                self.console.print("  shutting down ...", style=P.STYLES["command_info"])
                time.sleep(0.3)
                return False
        elif cmd == "new":
            # CMD-19: offered without confirmation after game over, with confirmation otherwise (UX-04)
            if self.game_over() or self.confirm(session, "Start a new game? The current save is deleted."):
                self.pending.clear()
                self.show_reply(self.send_order("new", args), line)
        else:
            self.show_reply(self.send_order(cmd, args), line)
        return True

    def run_headless(self) -> int:
        """No console (tests): stay subscribed until shutdown or the launcher vanishes (D-93)."""
        log.info("command terminal running headless (no tty)")
        while not self.listener.shutdown.is_set():
            time.sleep(0.25)
        self.listener.close()
        self.push.close(0)
        return 0


def main(cfg: GameConfig, pub_port: int, pull_port: int) -> int:
    return CommandTerminal(cfg, pub_port, pull_port).run()
