"""The engine process loop - T0, BUS-01...07, MSG-01...09, D-57, D-70, D-83.

Binds PUB and PULL, polls PULL, applies orders, resolves a turn on ``turn.end`` and
publishes ``turn.resolved`` with the full snapshot, autosaves, publishes ``state.hello``
every 2 s, and exits 0 on ``system.shutdown``.  The same class runs the headless sim:
with ``bus=False`` nothing is bound and ``resolve_and_publish`` only resolves and records.
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
from starbour.world.generate import game_rng

log = logging.getLogger("starbour.engine")

POLL_MS = 100
DRAIN_S = 0.3          # let the last publish leave the socket before closing (slow-joiner trap, inverse)
WARMUP_S = 10.0        # D-91: for the first seconds hello goes out every WARMUP_INTERVAL_S so windows draw at once
WARMUP_INTERVAL_S = 0.5


def pick_seed(cfg: GameConfig, requested: int | None) -> int:
    """C1: --seed overrides [world] seed; 'random' draws a fresh one (CFG-53)."""
    if requested is not None:
        return requested
    if cfg.seed is not None:
        return cfg.seed
    return random.SystemRandom().randrange(1, 2**31 - 1)


class Engine:
    """One game, one engine (BUS-04)."""

    def __init__(self, cfg: GameConfig, *, seed: int | None = None, save_path: Path | None = None, pub_port: int = 0,
                 pull_port: int = 0, bus: bool = True, ai_player: bool = False, max_turns: int | None = None,
                 recorder: Any = None, resume: bool = True, launcher_pid: int | None = None) -> None:
        self.cfg = cfg
        self.save_path = Path(save_path) if save_path else None
        self.want_pub, self.want_pull = pub_port, pull_port
        self.bus = bus
        self.ai_player = ai_player
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

    # ------------------------------------------------------------------ lifecycle
    def _new_game(self, seed: int) -> None:
        t0 = time.perf_counter()
        self.state = new_game(seed, self.cfg)
        self.rng = game_rng(seed)
        self._hello_bytes = None
        log.info("new game: seed %d, %d captains, %d sectors, generated in %.2f s", seed, len(self.state.captains),
                 len(self.state.world.sectors), time.perf_counter() - t0)

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
        if self.save_path is None:
            return
        try:
            S.save(self.state, self.rng, self.save_path, snapshot)
        except OSError as e:
            log.error("autosave failed: %s", e)

    # ------------------------------------------------------------------ the turn
    def resolve_and_publish(self) -> dict:
        """TP-01...08: resolve, publish turn.resolved (MSG-03), autosave (OV-12), record (C2)."""
        t0 = time.perf_counter()
        self.state, events = resolve_turn(self.state, self.cfg, self.rng, ai_player=self.ai_player)
        snapshot = self.state.snapshot()
        snap_hash = hash_snapshot(snapshot)
        payload = M.TurnResolved(self.state.turn_no, snapshot, events).to_dict()
        resolve_ms = (time.perf_counter() - t0) * 1000.0
        snap_bytes = self._publish(M.TURN_RESOLVED, payload)
        if snap_bytes == 0:
            snap_bytes = len(canonical_json(snapshot))
        self.state_changed()
        self.autosave(snapshot)
        self.event_log("system", "muted", f"autosaved turn {self.state.turn_no}")
        self.turns_done += 1
        stats = dict(self.state.last_stats)
        stats.update({"snapshot_hash": snap_hash, "snapshot_bytes": snap_bytes, "resolve_ms": resolve_ms})
        log.info("turn %d resolved in %.1f ms: %s; snapshot %d bytes", self.state.turn_no, resolve_ms,
                 stats.get("orders_executed"), snap_bytes)
        if self.recorder is not None:
            self.recorder.record_turn(self.state, events, stats)
        if self.state.game_over_turn is not None or self.state.mastery_turn is not None:
            self.request_stop(0)
        elif self.max_turns is not None and self.turns_done >= self.max_turns:
            self.request_stop(0)
        return stats

    # ------------------------------------------------------------------ messages
    def handle(self, topic: str, payload: dict) -> None:
        if topic == M.TURN_END:
            te = M.TurnEnd.from_dict(payload)
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
        spec = lookup(order.command)
        if spec is not None and spec.kind == IMMEDIATE:
            if order.command in ("help", "status", "fleet", "end", "quit"):
                self._publish(M.ORDER_REJECTED, M.OrderRejected(order.to_dict(), f"{order.command} is handled by the command terminal, not queued (OV-02b)").to_dict())
                return
            result = O.apply_immediate(self.state, order, self.cfg)
        else:
            result = O.submit(self.state, order, self.cfg)
        if isinstance(result, O.Rejected):
            log.info("rejected %s: %s", order.text(), result.reason)
            self._publish(M.ORDER_REJECTED, M.OrderRejected(order.to_dict(), result.reason).to_dict())
            return
        log.info("accepted %s (position %d, queued %d)", order.text(), result.queue_position, result.queued)
        self._publish(M.ORDER_ACCEPTED, M.OrderAccepted(order.to_dict(), result.queue_position, result.queued, result.text).to_dict())
        for ev in result.events:
            self.event_log(ev.get("type", "system"), ev.get("severity", "info"), ev.get("text", ""))
        self.state_changed()
        if spec is not None and spec.kind == IMMEDIATE:
            self.publish_hello()   # views redraw from the changed snapshot (D-91)

    def handle_new(self, order: Order) -> None:
        """CMD-19 / OV-09c: delete the save, regenerate, announce."""
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
                       resume: bool = True) -> int:
    """``python -m starbour --role engine`` (C1)."""
    from starbour import runtime
    engine = Engine(cfg, seed=seed, save_path=save_path, pub_port=pub_port, pull_port=pull_port, resume=resume,
                    launcher_pid=runtime.launcher_pid())
    try:
        engine.start()
    except Exception as e:   # noqa: BLE001 - a port in use is the usual cause
        log.error("engine could not start: %s", e)
        return 1
    runtime.start_parent_watchdog(on_exit=lambda: (engine.autosave(), engine.close()))
    return engine.run()
