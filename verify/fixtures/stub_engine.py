"""A stub ZeroMQ engine speaking CONTRACTS C3 (+ C9), for --selftest of the bus client and checks.

Modes:
  good             hello every `hello_interval`; turn.end → turn_no + 1 and a changed snapshot;
                   valid orders → order.accepted; move 99 0 → order.rejected naming CMD-06;
                   relative moves persist (move_remaining, orders — C9); 'orders' answers with a
                   listing (D-92); 'warp' is validated like CMD-07 / MV-02 (reach 3.07 at factor 3,
                   warpcore_power 3, Void = galaxy ≠ 3,4 or (sx + sy) % 7 == 0, universe 8 × 8)
  stuck_turn       turn.end → republishes the same turn_no and snapshot (gate must catch it)
  same_snapshot    turn_no advances in the payload but the snapshot never changes
  reject_no_id     rejections do not cite CMD-06
  no_reply         orders are swallowed
  no_hello         never publishes state.hello
  game_over        as good, and the player's ship dies at turn `die_at`: snapshot.game_over true,
                   game.over published, the save file deleted, every order but new/quit rejected
                   citing CMD-19, 'new' → fresh hello at turn 0 with a new seed (M2 § 3 smoke/21)
  game_over_leaky  dies at `die_at` but keeps accepting orders, keeps the save and never flags
                   game_over (the broken counterpart)
  battle           as good, plus a spawned AI Kestrel one cell from the player (C10 --spawn); an
                   accepted `fire phaser|torpedo <id>` opens a battle: snapshot ships[*].in_battle /
                   target and battles[] are set, the next turn.resolved carries type 'combat'
                   events (OUT-04) and the player's shields drop (M3 § 4 live/09)
  battle_leaky     accepts `fire` but opens nothing: no combat event, in_battle stays null, no
                   battles[] (the broken counterpart)

Runs as a thread (StubEngine) or as a script (prints the bound ports as JSON on stdout).
Opens no window.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
from _checks import chebyshev, flat_sector, signed, warp_reach  # noqa: E402

MODES = ("good", "stuck_turn", "same_snapshot", "reject_no_id", "no_reply", "no_hello", "game_over", "game_over_leaky", "battle", "battle_leaky")
BATTLE_MODES = ("battle", "battle_leaky")
ENGINE, WARP, GALAXY_SIZE, UNIVERSE_SIZE = 4, 3.0, (16, 16), (8, 8)
HOME = ((3, 4), (7, 9))
SPAWN_SHIP, SPAWN_CAPTAIN = "s-900", "c-900"


def default_snapshot(spawn: bool = False) -> dict:
    doc = json.loads((HERE / "snapshot_good.json").read_text(encoding="utf-8"))
    doc["turn_no"] = 0
    doc["game_over"] = False
    doc["battles"] = []
    for ship in doc["ships"].values():
        ship.setdefault("move_remaining", None)
        ship.setdefault("hazard", None)
        ship.setdefault("orders", [])
        ship.setdefault("in_battle", None)
        ship.setdefault("target", None)
    for cap in doc["captains"].values():
        cap.setdefault("visible_reputation", False)
    if spawn:                                      # C10 --spawn "kestrel … <cell 1 away> 0 10": an AI Kestrel beside the player
        pid = doc["player_id"]
        sid = doc["captains"][pid]["active_ship"]
        (gx, gy), (sx, sy), (cx, cy) = doc["ships"][sid]["position"]
        doc["captains"][SPAWN_CAPTAIN] = {"name": "Oriel Vance", "is_player": False, "gold": 2, "silver": 15, "fear": 0, "respect": 10, "food": 30, "water": 30,
                                          "ships": [SPAWN_SHIP], "active_ship": SPAWN_SHIP, "items": [], "is_master": False, "mutiny_countdown": None,
                                          "visible_reputation": False}
        doc["ships"][SPAWN_SHIP] = {"name": "Cinder Lark", "class": "kestrel", "captain": SPAWN_CAPTAIN, "torpedoes": 6, "phasers": 3.0, "shield_max": 60.0,
                                    "shield_current": 60.0, "engine": 4.0, "warp": 3.0, "position": [[gx, gy], [sx, sy], [cx + 1, cy]], "orders_queued": 0,
                                    "orders": [], "hazard": None, "move_remaining": None, "in_battle": None, "target": None}
    return doc


def is_void(galaxy: tuple[int, int], sector: tuple[int, int]) -> bool:
    return tuple(galaxy) != HOME[0] or (sector[0] + sector[1]) % 7 == 0


class StubEngine(threading.Thread):
    def __init__(self, mode: str = "good", pub_port: int = 0, pull_port: int = 0, hello_interval: float = 0.5,
                 snapshot: dict | None = None, save_path: Path | None = None, die_at: int = 7) -> None:
        super().__init__(daemon=True, name=f"stub-engine-{mode}")
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        self.mode = mode
        self.hello_interval = hello_interval
        self.snapshot = copy.deepcopy(snapshot) if snapshot else default_snapshot(spawn=mode in BATTLE_MODES)
        self.battle: dict | None = None          # the open battle record (battle mode)
        self.turn_no = int(self.snapshot.get("turn_no", 0))
        self.seed = int(self.snapshot.get("seed", 1234))
        self.save_path = Path(save_path) if save_path else None
        self.die_at = die_at
        self.game_over = False
        self.queued: dict[str, list[dict]] = {}
        self._want_pub, self._want_pull = pub_port, pull_port
        self.pub_port = self.pull_port = 0
        self.ready = threading.Event()
        self._halt = threading.Event()
        self.received: list[tuple[str, dict]] = []

    def stop(self) -> None:
        self._halt.set()
        self.join(5)

    # ---- helpers
    def _publish(self, pub, topic: str, payload: dict) -> None:
        pub.send_multipart([topic.encode("ascii"), json.dumps(payload).encode("utf-8")])

    def _hello(self) -> dict:
        return {"turn_no": self.turn_no, "snapshot": self.snapshot, "seed": self.seed, "version": "0.1.0"}

    def _player(self) -> tuple[str, dict]:
        cap = self.snapshot["captains"][self.snapshot["player_id"]]
        sid = cap["active_ship"]
        return sid, self.snapshot["ships"][sid]

    def _save(self) -> None:
        if self.save_path is not None:
            self.save_path.parent.mkdir(parents=True, exist_ok=True)
            self.save_path.write_text(json.dumps({"snapshot": self.snapshot, "rng_state": [1]}), encoding="utf-8")

    def _apply_moves(self, sid: str, ship: dict) -> None:
        """MV-01 / D-98: step floor(engine) cells toward the target; persist the rest."""
        w, h = self.snapshot["sizes"]["sector"]
        (gx, gy), (sx, sy), (cx, cy) = ship["position"]
        q = self.queued.get(sid, [])
        move = next((o for o in q if o["command"] == "move"), None)
        if move is None:
            if ship.get("move_remaining"):
                tx, ty = cx + ship["move_remaining"][0], cy + ship["move_remaining"][1]
            else:
                ship["position"] = [[gx, gy], [sx, sy], [(cx + 1) % w, cy]]      # the M1 stub's drift keeps hashes changing
                return
        else:
            a0, a1 = move["args"][0], move["args"][1]
            rel = a0[0] in "+-" or a1[0] in "+-"
            tx, ty = (cx + int(a0), cy + int(a1)) if rel else (int(a0), int(a1))
        dx, dy = tx - cx, ty - cy
        step = min(ENGINE, max(abs(dx), abs(dy)))
        nx = cx + (max(-step, min(step, dx)))
        ny = cy + (max(-step, min(step, dy)))
        ship["position"] = [[gx, gy], [sx, sy], [nx, ny]]
        rx, ry = tx - nx, ty - ny
        self.queued[sid] = [o for o in q if o["command"] != "move"]
        if rx or ry:
            ship["move_remaining"] = [rx, ry]
            self.queued[sid].insert(0, {"command": "move", "args": [signed(rx), signed(ry)], "persisting": True})
        else:
            ship["move_remaining"] = None
        ship["orders"] = [{"command": o["command"], "args": list(o["args"])} for o in self.queued[sid]]
        ship["orders_queued"] = len(ship["orders"])

    def _advance(self) -> tuple[dict, dict | None]:
        """Resolve a turn; return (turn.resolved payload, game.over payload or None)."""
        if self.mode == "stuck_turn":
            return {"turn_no": self.turn_no, "snapshot": self.snapshot, "events": []}, None
        self.turn_no += 1
        over = None
        events = [{"phase": "TP-02", "type": "movement", "severity": "info", "text": "Lantern moved", "ids": ["MV-01"]},
                  {"phase": "TP-06", "type": "upkeep", "severity": "info", "text": "upkeep", "ids": ["SUP-01"]}]
        if self.mode != "same_snapshot":
            self.snapshot["turn_no"] = self.turn_no
            pid = self.snapshot["player_id"]
            cap = self.snapshot["captains"][pid]
            cap["food"] = max(0, cap["food"] - 1)
            cap["water"] = max(0, cap["water"] - 1)
            sid, ship = self._player()
            self._apply_moves(sid, ship)
            for other_sid, other in self.snapshot["ships"].items():
                if other_sid != sid:
                    self.queued.pop(other_sid, None)
            if self.mode == "battle" and self.battle is not None:        # TP-04: one phaser exchange each way at distance 1 (CM-04: 3 × 5 × 2/3 = 10)
                enemy = self.snapshot["ships"][SPAWN_SHIP]
                for attacker, defender in ((ship, enemy), (enemy, ship)):
                    defender["shield_current"] = max(0.0, float(defender["shield_current"]) - 10.0)
                    events.append({"phase": "TP-04", "type": "combat", "severity": "alert", "ids": ["OUT-04"],
                                   "text": f"{attacker['name']} → {defender['name']}: phaser hit, 10.0 damage, shields left {defender['shield_current']:g}"})
                self.snapshot["battles"] = [dict(self.battle)]
            if self.mode in ("game_over", "game_over_leaky") and self.turn_no == self.die_at and not self.game_over:
                self.game_over = True
                cell = ship["position"][2]
                events.append({"phase": "TP-03", "type": "destroyed", "severity": "alert",
                               "text": f"{ship['name']} was pulled into the star at {cell[0]},{cell[1]} — ship lost", "ids": ["OUT-16"]})
                if self.mode == "game_over":
                    self.snapshot["game_over"] = True
                    over = {"turn_no": self.turn_no, "cause": f"pulled into the star at {cell[0]},{cell[1]} in sector 3,4/7,9",
                            "summary": {"turns": self.turn_no, "sectors_visited": 1, "mutinies": 0, "fear": cap["fear"], "respect": cap["respect"],
                                        "gold": cap["gold"], "silver": cap["silver"]}}
                else:
                    over = {"turn_no": self.turn_no, "cause": "pulled into the star", "summary": {"turns": self.turn_no}}
        return {"turn_no": self.turn_no, "snapshot": self.snapshot, "events": events}, over

    def _order_reply(self, order: dict) -> tuple[str, dict] | None:
        if self.mode == "no_reply":
            return None
        command = str(order.get("command", ""))
        args = [str(a) for a in (order.get("args") or [])]
        sid, ship = self._player()
        if self.game_over and self.mode == "game_over" and command not in ("new", "quit"):
            return "order.rejected", {"order": order, "reason": "game over — type new to start again (CMD-19)"}
        if command == "fire":
            weapon = args[0] if args else ""
            target = args[1] if len(args) > 1 else ""
            if weapon not in ("phaser", "torpedo") or target not in self.snapshot["ships"]:
                return "order.rejected", {"order": order, "reason": f"fire needs torpedo|phaser <target> (CMD-08); {target!r} is not a ship"}
            if self.mode == "battle":
                pid = self.snapshot["player_id"]
                enemy_cid = self.snapshot["ships"][target]["captain"]
                self.battle = {"id": "b-001", "sides": [pid, enemy_cid], "opened_turn": self.turn_no + 1, "opened_by": pid,
                               "strength_at_open": {pid: 255.0, enemy_cid: 255.0}, "ended": None, "victor": None, "transfer": None}
                ship["in_battle"], ship["target"] = "b-001", target
                self.snapshot["ships"][target]["in_battle"], self.snapshot["ships"][target]["target"] = "b-001", sid
                self.snapshot["battles"] = [dict(self.battle)]
            return "order.accepted", {"order": order, "queue_position": 1, "queued": 1, "text": f"fire {weapon} at {target} queued"}
        if command == "new":
            self.seed += 1
            self.snapshot = default_snapshot(spawn=self.mode in BATTLE_MODES)
            self.snapshot["seed"] = self.seed
            self.turn_no = 0
            self.game_over = False
            self.queued = {}
            self._save()
            return "order.accepted", {"order": order, "queue_position": 0, "queued": 0, "text": f"new game started with seed {self.seed}"}
        if command == "orders":
            q = self.queued.get(sid, [])
            text = "\n".join(f"{i + 1}. {o['command']} {' '.join(o['args'])}" + (" (persisting)" if o.get("persisting") else "")
                             for i, o in enumerate(q)) or "no orders queued"
            return "order.accepted", {"order": order, "queue_position": 0, "queued": len(q), "text": text}
        if command == "stop":
            self.queued[sid] = [o for o in self.queued.get(sid, []) if o["command"] != "move"]
            ship["move_remaining"] = None
            ship["orders"] = [{"command": o["command"], "args": list(o["args"])} for o in self.queued[sid]]
            return "order.accepted", {"order": order, "queue_position": 0, "queued": len(self.queued[sid]), "text": "all stop"}
        if command == "warp":
            try:
                gx, gy, sx, sy = (int(a) for a in args[:4])
                factor = float(args[4]) if len(args) > 4 else WARP
            except (ValueError, IndexError):
                return "order.rejected", {"order": order, "reason": "warp needs <gx gy> <sx sy> [factor] (CMD-07)"}
            if factor > WARP:
                return "order.rejected", {"order": order, "reason": f"factor {factor:g} exceeds warpcore_power {WARP:g} (CMD-07)"}
            if not (0 <= gx < UNIVERSE_SIZE[0] and 0 <= gy < UNIVERSE_SIZE[1] and 0 <= sx < GALAXY_SIZE[0] and 0 <= sy < GALAXY_SIZE[1]):
                return "order.rejected", {"order": order, "reason": f"galaxy {gx},{gy} sector {sx},{sy} is outside the universe (CMD-07)"}
            if is_void((gx, gy), (sx, sy)):
                return "order.rejected", {"order": order, "reason": f"destination {gx},{gy}/{sx},{sy} is Void (CMD-07)"}
            d = chebyshev(flat_sector((gx, gy), (sx, sy), GALAXY_SIZE), flat_sector(HOME[0], HOME[1], GALAXY_SIZE))
            reach = warp_reach(factor, 16, 4)
            if d > reach:
                return "order.rejected", {"order": order, "reason": f"destination {d} sectors away is beyond the reach {reach:.2f} for factor {factor:g} (CMD-07)"}
            self.queued.setdefault(sid, []).append({"command": "warp", "args": args})
            return "order.accepted", {"order": order, "queue_position": len(self.queued[sid]), "queued": len(self.queued[sid]), "text": f"warp queued ({d} sectors)"}
        if command == "move":
            w, h = self.snapshot["sizes"]["sector"]
            cx, cy = ship["position"][2]
            try:
                a0, a1 = args[0], args[1]
                rel = a0[0] in "+-" or a1[0] in "+-"
                x, y = (cx + int(a0), cy + int(a1)) if rel else (int(a0), int(a1))
            except (ValueError, IndexError):
                return "order.rejected", {"order": order, "reason": "move needs <x y> or <dx dy> (CMD-06)"}
            if 0 <= x < w and 0 <= y < h:
                q = [o for o in self.queued.get(sid, []) if o["command"] != "move"]
                q.append({"command": "move", "args": args[:2]})
                self.queued[sid] = q
                ship["orders"] = [{"command": o["command"], "args": list(o["args"])} for o in q]
                return "order.accepted", {"order": order, "queue_position": len(q), "queued": len(q), "text": f"move queued → {x},{y}"}
            if self.mode == "reject_no_id":
                return "order.rejected", {"order": order, "reason": f"target {x},{y} is outside the sector"}
            return "order.rejected", {"order": order, "reason": f"target {x},{y} is outside the sector {w}x{h} (CMD-06)"}
        return "order.accepted", {"order": order, "queue_position": 1, "queued": 1, "text": f"{command} queued"}

    def run(self) -> None:
        import zmq
        ctx = zmq.Context()
        pub = ctx.socket(zmq.PUB)
        pull = ctx.socket(zmq.PULL)
        for s in (pub, pull):
            s.setsockopt(zmq.LINGER, 0)
        self.pub_port = pub.bind_to_random_port("tcp://127.0.0.1") if not self._want_pub else (pub.bind(f"tcp://127.0.0.1:{self._want_pub}") or self._want_pub)
        self.pull_port = pull.bind_to_random_port("tcp://127.0.0.1") if not self._want_pull else (pull.bind(f"tcp://127.0.0.1:{self._want_pull}") or self._want_pull)
        poller = zmq.Poller()
        poller.register(pull, zmq.POLLIN)
        self.ready.set()
        next_hello = 0.0
        try:
            while not self._halt.is_set():
                now = time.perf_counter()
                if self.mode != "no_hello" and now >= next_hello:
                    self._publish(pub, "state.hello", self._hello())
                    next_hello = now + self.hello_interval
                if not poller.poll(50):
                    continue
                frames = pull.recv_multipart()
                if len(frames) != 2:
                    continue
                topic = frames[0].decode("ascii", "replace")
                try:
                    payload = json.loads(frames[1].decode("utf-8"))
                except ValueError:
                    continue
                self.received.append((topic, payload))
                if topic == "turn.end":
                    if self.game_over and self.mode == "game_over":
                        continue                                     # nothing resolves after game over until 'new'
                    resolved, over = self._advance()
                    self._publish(pub, "turn.resolved", resolved)
                    if over is not None and self.mode == "game_over":
                        if self.save_path is not None and self.save_path.exists():
                            self.save_path.unlink()                  # OV-09c: drop the save before announcing
                        self._publish(pub, "game.over", over)
                    else:
                        self._save()
                    if over is not None and self.mode == "game_over_leaky":
                        self._publish(pub, "game.over", over)
                elif topic == "order.submitted":
                    reply = self._order_reply(payload)
                    if reply:
                        self._publish(pub, *reply)
                        if str(payload.get("command")) in ("new", "orders", "stop"):
                            self._publish(pub, "state.hello", self._hello())
                            next_hello = time.perf_counter() + self.hello_interval
                elif topic == "system.shutdown":
                    self._publish(pub, "system.shutdown", payload)
                    break
        finally:
            pub.close(0)
            pull.close(0)
            ctx.term()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", default="good", choices=MODES)
    ap.add_argument("--pub-port", type=int, default=0)
    ap.add_argument("--pull-port", type=int, default=0)
    ap.add_argument("--seconds", type=float, default=30.0, help="how long to serve")
    ap.add_argument("--save-path", type=Path, default=None)
    a = ap.parse_args()
    eng = StubEngine(a.mode, a.pub_port, a.pull_port, save_path=a.save_path)
    eng.start()
    eng.ready.wait(5)
    print(json.dumps({"pub_port": eng.pub_port, "pull_port": eng.pull_port, "mode": a.mode}), flush=True)
    time.sleep(a.seconds)
    eng.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
