"""The engine process loop - T0, BUS-01...07, MSG-01...09, D-57, D-70, D-83, D-104, D-105, D-125, D-126.

Binds PUB and PULL, polls PULL, applies orders, resolves a turn on ``turn.end`` and
publishes ``turn.resolved`` with the full snapshot, autosaves, publishes ``state.hello``
every 2 s, and exits 0 on ``system.shutdown``.  The same class runs the headless sim:
with ``bus=False`` nothing is bound and ``resolve_and_publish`` only resolves and records.

Game over (OV-09b, OV-09c, CMD-19, C9): the turn the player's active ship is destroyed the
save is deleted *before* ``game.over`` is published; afterwards every order except ``new``
and ``quit`` is rejected and ``turn.end`` resolves nothing.  The in-process sim stops there
(D-106); the bus engine stays up until ``new`` or a shutdown (D-107).

Debug affordances (C10): ``--spawn "<class> gx gy sx sy cx cy [fear] [respect]"`` places an
AI captain before turn 1 (D-126) and ``--ai passive`` keeps every AI captain from opening a
battle (D-125).  Both are logged as WARNING and ignored on a resumed game.
"""
from __future__ import annotations

import logging
import random
import time
from pathlib import Path
from typing import Any

from starbour import __version__
from starbour.bus import messages as M
from starbour.commands.catalogue import lookup
from starbour.commands.parse import IMMEDIATE, CommandError, Order, parse_new, parse_tokens
from starbour.config import GameConfig
from starbour.engine import orders as O
from starbour.engine import save as S
from starbour.engine.phases import resolve_turn
from starbour.engine.state import GameState, canonical_json, hash_snapshot, new_game
from starbour.entities.ship import Position
from starbour.world.generate import game_rng

log = logging.getLogger("starbour.engine")

POLL_MS = 100
DRAIN_S = 0.3          # let the last publish leave the socket before closing (slow-joiner trap, inverse)
WARMUP_S = 10.0        # D-91: for the first seconds hello goes out every WARMUP_INTERVAL_S so windows draw at once
WARMUP_INTERVAL_S = 0.5
GAME_OVER_REASON = "game over - type new to start again (CMD-19)"
T1_ONLY = ("help", "status", "fleet", "shields", "end", "quit")   # answered by T1 from its snapshot (D-92)
AI_MODES = ("normal", "passive")


def pick_seed(cfg: GameConfig, requested: int | None) -> int:
    """C1: --seed overrides [world] seed; 'random' draws a fresh one (CFG-53)."""
    if requested is not None:
        return requested
    if cfg.seed is not None:
        return cfg.seed
    return random.SystemRandom().randrange(1, 2**31 - 1)


def parse_teleport(text: str) -> Position:
    """C9 / D-104: ``--teleport "gx gy sx sy cx cy"`` -> ((gx, gy), (sx, sy), (cx, cy))."""
    parts = text.replace(",", " ").split()
    if len(parts) != 6:
        raise ValueError(f'--teleport needs six integers "gx gy sx sy cx cy", got {text!r} (C9, D-104)')
    try:
        g = [int(p) for p in parts]
    except ValueError:
        raise ValueError(f'--teleport needs six integers "gx gy sx sy cx cy", got {text!r} (C9, D-104)') from None
    return (g[0], g[1]), (g[2], g[3]), (g[4], g[5])


def parse_spawn(text: str) -> tuple[str, Position, int | None, int | None]:
    """C10 / D-126: ``--spawn "<class> gx gy sx sy cx cy [fear] [respect]"`` -> (class, position, fear, respect)."""
    parts = text.replace(",", " ").split()
    if len(parts) not in (7, 8, 9):
        raise ValueError(f'--spawn needs "<class> gx gy sx sy cx cy [fear] [respect]", got {text!r} (C10, D-126)')
    try:
        nums = [int(p) for p in parts[1:]]
    except ValueError:
        raise ValueError(f'--spawn needs integers after the class name, got {text!r} (C10, D-126)') from None
    fear = nums[6] if len(nums) > 6 else None
    respect = nums[7] if len(nums) > 7 else None
    return parts[0].lower(), ((nums[0], nums[1]), (nums[2], nums[3]), (nums[4], nums[5])), fear, respect


class Engine:
    """One game, one engine (BUS-04)."""

    def __init__(self, cfg: GameConfig, *, seed: int | None = None, save_path: Path | None = None, pub_port: int = 0,
                 pull_port: int = 0, bus: bool = True, ai_player: bool = False, max_turns: int | None = None,
                 recorder: Any = None, resume: bool = True, launcher_pid: int | None = None,
                 teleport: Position | None = None, spawns: list[str] | None = None, ai_passive: bool = False) -> None:
        self.cfg = cfg
        self.save_path = Path(save_path) if save_path else None
        self.want_pub, self.want_pull = pub_port, pull_port
        self.bus = bus
        self.ai_player = ai_player
        self.ai_passive = ai_passive
        self.max_turns = max_turns
        self.recorder = recorder
        self.launcher_pid = launcher_pid
        self.pub = None
        self.pull = None
        self.pub_port = self.pull_port = 0
        self.turns_done = 0
        self.stopping = False
        self.exit_code = 0
        self._hello_bytes: bytes | None = None
        self.state: GameState
        self.rng: random.Random
        self.resumed = False
        if resume and self.save_path is not None and self.save_path.is_file():
            try:
                self.state, self.rng = S.load_full(self.save_path, cfg)
                self.resumed = True
                log.info("resumed %s at turn %d (seed %d)", self.save_path, self.state.turn_no, self.state.seed)
            except (OSError, ValueError, KeyError) as e:
                log.error("save %s unreadable (%s) - starting a new game", self.save_path, e)
                self._new_game(pick_seed(cfg, seed))
        else:
            self._new_game(pick_seed(cfg, seed))
        if teleport is not None:
            self.apply_teleport(teleport)
        for text in spawns or []:
            self.apply_spawn(text)
        if ai_passive:
            log.warning("DEBUG --ai passive (D-125): AI captains never open a battle this game")

    # ------------------------------------------------------------------ lifecycle
    def _new_game(self, seed: int) -> None:
        t0 = time.perf_counter()
        self.state = new_game(seed, self.cfg)
        self.rng = game_rng(seed)
        self._hello_bytes = None
        log.info("new game: seed %d, %d captains, %d sectors, generated in %.2f s", seed, len(self.state.captains),
                 len(self.state.world.sectors), time.perf_counter() - t0)

    def apply_teleport(self, position: Position) -> None:
        """D-104 / D-110 debug affordance: the active ship is placed before turn 1; ignored on a resumed game."""
        if self.state.turn_no != 0:
            log.warning("--teleport ignored: the game resumed at turn %d (D-104 places the ship before turn 1)", self.state.turn_no)
            return
        ship = self.state.teleport(position)
        self._hello_bytes = None
        log.warning("DEBUG teleport (D-104): %s placed at galaxy %s sector %s cell %s", ship.name, ship.galaxy, ship.sector, ship.cell)

    def apply_spawn(self, text: str) -> None:
        """D-126 debug affordance: an AI captain with one ship before turn 1; ignored on a resumed game."""
        if self.state.turn_no != 0:
            log.warning("--spawn %r ignored: the game resumed at turn %d (D-126 spawns before turn 1)", text, self.state.turn_no)
            return
        cls, position, fear, respect = parse_spawn(text)
        cap, ship = self.state.spawn(cls, position, fear, respect)
        self._hello_bytes = None
        log.warning("DEBUG spawn (D-126): %s (%s) in the %s (%s, %s) at galaxy %s sector %s cell %s, fear %d respect %d",
                    cap.name, cap.id, ship.name, ship.id, ship.cls, ship.galaxy, ship.sector, ship.cell, cap.fear, cap.respect)

    def start(self) -> None:
        """Bind the sockets (bus mode), save the opening state, say hello."""
        if self.bus:
            from starbour.bus.zmq_io import Publisher, Puller
            self.pub = Publisher(self.want_pub)
            self.pull = Puller(self.want_pull)
            self.pub_port, self.pull_port = self.pub.port, self.pull.port
            log.info("bound PUB tcp://127.0.0.1:%d PULL tcp://127.0.0.1:%d", self.pub_port, self.pull_port)
        if self.save_path is not None:
            self.autosave()
        self.publish_hello()

    def close(self) -> None:
        if self.pub is not None or self.pull is not None:
            time.sleep(DRAIN_S)
        for sock in (self.pub, self.pull):
            if sock is not None:
                sock.close(0)
        self.pub = self.pull = None

    def request_stop(self, code: int = 0) -> None:
        self.stopping = True
        self.exit_code = code

    # ------------------------------------------------------------------ publishing
    def _publish(self, topic: str, payload: dict) -> int:
        if self.pub is None:
            return 0
        return self.pub.publish(topic, payload)

    def hello_payload(self) -> dict:
        return M.StateHello(self.state.turn_no, self.state.snapshot(), self.state.seed, __version__).to_dict()

    def publish_hello(self) -> None:
        """D-70 / D-83: the latest snapshot; the bytes are cached until the state changes."""
        if self.pub is None:
            return
        if self._hello_bytes is None:
            self._hello_bytes = canonical_json(self.hello_payload())
        self.pub.publish_raw(M.STATE_HELLO, self._hello_bytes)

    def state_changed(self) -> None:
        self._hello_bytes = None

    def event_log(self, type_: str, severity: str, text: str) -> None:
        self._publish(M.EVENT_LOG, M.EventLog(self.state.turn_no, type_, severity, text).to_dict())

    def autosave(self, snapshot: dict | None = None) -> None:
        """OV-12 autosave; after game over the save stays deleted (OV-09c) whatever asks for one."""
        if self.save_path is None:
            return
        if self.state.game_over:
            S.delete(self.save_path)
            return
        try:
            S.save(self.state, self.rng, self.save_path, snapshot)
        except OSError as e:
            log.error("autosave failed: %s", e)

    def game_over_payload(self) -> dict:
        """MSG-09 / C9: {turn_no, cause, summary}."""
        over = self.state.last_stats.get("game_over") or {}
        cap = self.state.player
        summary = {"turns": self.state.turn_no, "sectors_visited": self.state.sectors_visited(), "mutinies": self.state.player_mutinies,
                   "fear": cap.fear, "respect": cap.respect, "gold": cap.gold, "silver": cap.silver}
        return M.GameEnd(self.state.turn_no, str(over.get("cause") or "ship destroyed"), summary).to_dict()

    # ------------------------------------------------------------------ the turn
    def resolve_and_publish(self) -> dict:
        """TP-01...08: resolve, publish turn.resolved (MSG-03), autosave (OV-12) or game.over (MSG-09), record (C2)."""
        t0 = time.perf_counter()
        self.state, events = resolve_turn(self.state, self.cfg, self.rng, ai_player=self.ai_player, ai_passive=self.ai_passive)
        snapshot = self.state.snapshot()
        snap_hash = hash_snapshot(snapshot)
        payload = M.TurnResolved(self.state.turn_no, snapshot, events).to_dict()
        resolve_ms = (time.perf_counter() - t0) * 1000.0
        snap_bytes = self._publish(M.TURN_RESOLVED, payload)
        if snap_bytes == 0:
            snap_bytes = len(canonical_json(snapshot))
        self.state_changed()
        ended = self.state.game_over_turn == self.state.turn_no
        if ended:
            # OV-09c / M2 section 5: drop the save before announcing, so a crash cannot leave a resurrectable game
            if self.save_path is not None:
                S.delete(self.save_path)
            over = self.game_over_payload()
            self._publish(M.GAME_OVER, over)
            self.event_log("game", "alert", f"GAME OVER at turn {self.state.turn_no}: {over['cause']}. Type 'new' to start again (CMD-19, OUT-16)")
            log.info("game over at turn %d: %s", self.state.turn_no, over["cause"])
            self.publish_hello()
        else:
            self.autosave(snapshot)
            self.event_log("system", "muted", f"autosaved turn {self.state.turn_no}")
        self.turns_done += 1
        stats = dict(self.state.last_stats)
        stats.update({"snapshot_hash": snap_hash, "snapshot_bytes": snap_bytes, "resolve_ms": resolve_ms})
        log.info("turn %d resolved in %.1f ms: %s; snapshot %d bytes", self.state.turn_no, resolve_ms,
                 stats.get("orders_executed"), snap_bytes)
        if self.recorder is not None:
            self.recorder.record_turn(self.state, events, stats)
        if self.state.game_over or self.state.mastery_turn is not None:
            if not self.bus:
                self.request_stop(0)      # D-106: the in-process sim stops the turn the game ends
        elif self.max_turns is not None and self.turns_done >= self.max_turns:
            self.request_stop(0)
        return stats

    # ------------------------------------------------------------------ messages
    def handle(self, topic: str, payload: dict) -> None:
        if topic == M.TURN_END:
            te = M.TurnEnd.from_dict(payload)
            if self.state.game_over:
                log.info("turn.end ignored after game over (CMD-19)")
                self.event_log("game", "alert", GAME_OVER_REASON)
                self.publish_hello()
                return
            if te.turn_no != self.state.turn_no:
                log.warning("turn.end carries turn_no %d, engine is at %d - resolving anyway", te.turn_no, self.state.turn_no)
            self.resolve_and_publish()
        elif topic == M.ORDER_SUBMITTED:
            self.handle_order(payload)
        elif topic == M.VIEW_CHANGED:
            vc = M.ViewChanged.from_dict(payload)
            self.state.view = vc.to_dict()
            self.state_changed()
            self._publish(M.VIEW_CHANGED, vc.to_dict())
            self.publish_hello()
        elif topic == M.SYSTEM_SHUTDOWN:
            reason = M.SystemShutdown.from_dict(payload).reason
            log.info("system.shutdown: %s", reason)
            self.autosave()
            self._publish(M.SYSTEM_SHUTDOWN, {"reason": reason})
            self.request_stop(0)
        else:
            log.debug("ignoring topic %s", topic)

    def apply_order(self, order: Order) -> O.Accepted | O.Rejected:
        """Route a parsed order: immediate ones are applied at once (D-92), queued ones validated and queued."""
        if self.state.game_over and order.command not in ("new", "quit"):
            return O.Rejected(order, GAME_OVER_REASON)
        spec = lookup(order.command)
        if spec is not None and spec.kind == IMMEDIATE:
            if order.command in T1_ONLY:
                return O.Rejected(order, f"{order.command} is handled by the command terminal, not queued (OV-02b)")
            return O.apply_immediate(self.state, order, self.cfg)
        return O.submit(self.state, order, self.cfg)

    def handle_order(self, payload: dict) -> None:
        """MSG-01 -> MSG-06 / MSG-07.  Immediate state-changing commands are applied at once (D-92)."""
        try:
            sub = M.OrderSubmitted.from_dict(payload)
        except (KeyError, ValueError, TypeError) as e:
            self._publish(M.ORDER_REJECTED, M.OrderRejected(payload, f"malformed order.submitted: {e} (MSG-01)").to_dict())
            return
        ship_id = sub.ship_id or (self.state.player.active_ship or "")
        try:
            order = parse_tokens([sub.command, *sub.args], ship_id)
        except CommandError as e:
            self._publish(M.ORDER_REJECTED, M.OrderRejected(sub.to_dict(), str(e)).to_dict())
            return
        if order.command == "new":
            self.handle_new(order)
            return
        result = self.apply_order(order)
        if isinstance(result, O.Rejected):
            log.info("rejected %s: %s", order.text(), result.reason)
            self._publish(M.ORDER_REJECTED, M.OrderRejected(order.to_dict(), result.reason).to_dict())
            return
        log.info("accepted %s (position %d, queued %d)", order.text(), result.queue_position, result.queued)
        self._publish(M.ORDER_ACCEPTED, M.OrderAccepted(order.to_dict(), result.queue_position, result.queued, result.text).to_dict())
        for ev in result.events:
            self.event_log(ev.get("type", "system"), ev.get("severity", "info"), ev.get("text", ""))
        self.state_changed()
        self.publish_hello()   # D-91 / D-99: views (PNL-07) redraw on every accepted or cancelled order

    def apply_script_line(self, line: str) -> tuple[bool, str]:
        """D-104 / C10 ``--player-script``: orders for the player's active ship, applied as T1 would send them;
        several orders on one line are separated by ';' (one turn).  Returns (all accepted, the replies joined)."""
        replies: list[str] = []
        ok_all = True
        for part in [p.strip() for p in line.split(";")]:
            if not part:
                continue
            ok, text = self._apply_script_order(part)
            ok_all = ok_all and ok
            replies.append(text)
        return ok_all, " | ".join(replies) if replies else "script line empty"

    def _apply_script_order(self, text: str) -> tuple[bool, str]:
        ship_id = self.state.player.active_ship or ""
        try:
            order = parse_tokens(text.split(), ship_id)
        except CommandError as e:
            return False, f"script order {text!r} not parsed: {e}"
        if order.command == "new":
            return False, "script order 'new' is not supported in the sim"
        result = self.apply_order(order)
        if isinstance(result, O.Rejected):
            return False, f"script order {order.text()!r} rejected: {result.reason}"
        return True, f"script order {order.text()!r} accepted" + (f": {result.text}" if result.text else "")

    def handle_new(self, order: Order) -> None:
        """CMD-19 / OV-09c: delete the save, regenerate, announce (also the way out of game over, D-105)."""
        try:
            seed = parse_new(order.args)
        except CommandError as e:
            self._publish(M.ORDER_REJECTED, M.OrderRejected(order.to_dict(), str(e)).to_dict())
            return
        if self.save_path is not None:
            S.delete(self.save_path)
        self._new_game(pick_seed(self.cfg, seed) if seed is not None else random.SystemRandom().randrange(1, 2**31 - 1))
        self.turns_done = 0
        self.autosave()
        self._publish(M.ORDER_ACCEPTED, M.OrderAccepted(order.to_dict(), 0, 0, f"new game started with seed {self.state.seed}").to_dict())
        self.event_log("system", "highlight", f"New game: seed {self.state.seed}; {len(self.state.captains) - 1} AI captains")
        self.publish_hello()

    # ------------------------------------------------------------------ loop
    def run(self) -> int:
        """Serve until stopped; returns the exit code.  Sockets are closed on the way out (LINGER 0)."""
        if self.pull is None and self.bus:
            self.start()
        started = time.perf_counter()
        next_hello = started + WARMUP_INTERVAL_S
        try:
            while not self.stopping:
                got = self.pull.recv(POLL_MS) if self.pull is not None else None
                if got is not None:
                    topic, payload = got
                    try:
                        self.handle(topic, payload)
                    except Exception:   # noqa: BLE001 - one bad message must not kill the game
                        log.exception("error handling %s", topic)
                        self.event_log("system", "error", f"engine error handling {topic}; see logs/engine.log")
                if got is None and self.pull is None:
                    time.sleep(POLL_MS / 1000.0)
                now = time.perf_counter()
                if now >= next_hello:
                    self.publish_hello()
                    next_hello = now + (WARMUP_INTERVAL_S if now - started < WARMUP_S else M.HELLO_INTERVAL_S)
                if self.launcher_pid is not None and not _pid_exists(self.launcher_pid):
                    log.warning("launcher pid %d gone - exiting (D-59)", self.launcher_pid)
                    self.request_stop(0)
        finally:
            self.close()
        return self.exit_code


def _pid_exists(pid: int) -> bool:
    import psutil
    return psutil.pid_exists(pid)


def run_engine_process(cfg: GameConfig, *, seed: int | None, pub_port: int, pull_port: int, save_path: Path,
                       resume: bool = True, teleport: Position | None = None, spawns: list[str] | None = None,
                       ai_passive: bool = False) -> int:
    """``python -m starbour --role engine`` (C1); ``--teleport`` / ``--spawn`` / ``--ai`` are the debug affordances (D-110, D-125, D-126)."""
    from starbour import runtime
    try:
        engine = Engine(cfg, seed=seed, save_path=save_path, pub_port=pub_port, pull_port=pull_port, resume=resume,
                        launcher_pid=runtime.launcher_pid(), teleport=teleport, spawns=spawns, ai_passive=ai_passive)
        engine.start()
    except Exception as e:   # noqa: BLE001 - a port in use or a bad debug flag is the usual cause
        log.error("engine could not start: %s", e)
        return 1
    runtime.start_parent_watchdog(on_exit=lambda: (engine.autosave(), engine.close()))
    return engine.run()
