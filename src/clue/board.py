"""Clue board geometry, distances and legal path construction.

Conservative movement rules (an illegal move eliminates us):
  * every step is orthogonal, onto a walkable '-' square, never a start square,
    never an occupied square, never a square already on the path;
  * door squares are only used as the final square (to enter that room) or as
    the first square when leaving a room through its own door;
  * corridor moves use exactly the dice total; room entries may use fewer.
"""
from collections import deque


def rc(cell):
    return (int(cell["row"]), int(cell["column"]))


class Board:
    def __init__(self, board):
        self.width = int(board["width"])
        self.height = int(board["height"])
        rows = board["rows"]
        self.walk = set()
        for r, line in enumerate(rows):
            for c, ch in enumerate(line):
                if ch == "-":
                    self.walk.add((r, c))
        self.starts = {rc(v) for v in (board.get("starts") or {}).values()}
        self.door_rooms = {}   # corridor cell -> [room ids]
        self.room_doors = {}   # room id -> [corridor cells]
        for d in board.get("doors") or []:
            cell = rc(d["corridor"])
            room = d["room"]
            self.door_rooms.setdefault(cell, [])
            if room not in self.door_rooms[cell]:
                self.door_rooms[cell].append(room)
            self.room_doors.setdefault(room, [])
            if cell not in self.room_doors[room]:
                self.room_doors[room].append(cell)
        for room in board.get("rooms") or []:
            rid = room.get("id")
            if rid and rid not in self.room_doors:
                self.room_doors[rid] = []
        self.passages = {}
        for p in board.get("secretPassages") or []:
            self.passages.setdefault(p["from"], p["to"])
            self.passages.setdefault(p["to"], p["from"])
        self.rooms = sorted(self.room_doors)
        self._room_dist_cache = {}

    def neighbors(self, cell):
        r, c = cell
        for nb in ((r - 1, c), (r, c - 1), (r, c + 1), (r + 1, c)):
            if nb in self.walk:
                yield nb

    def is_door(self, cell):
        return cell in self.door_rooms

    # -- distances (ignoring pawns; for planning future turns) -------------
    def bfs(self, sources, blocked=frozenset(), through_doors=True):
        dist = {}
        q = deque()
        for s in sources:
            if s in self.walk and s not in dist:
                dist[s] = 0
                q.append(s)
        while q:
            cur = q.popleft()
            if not through_doors and cur in self.door_rooms and dist[cur] > 0:
                continue
            for nb in self.neighbors(cur):
                if nb in dist or nb in blocked or nb in self.starts:
                    continue
                dist[nb] = dist[cur] + 1
                q.append(nb)
        return dist

    def dist_to_room(self, room):
        """corridor cell -> steps needed to stand on one of `room`'s doors."""
        if room not in self._room_dist_cache:
            self._room_dist_cache[room] = self.bfs(self.room_doors.get(room, []))
        return self._room_dist_cache[room]

    def room_to_room(self, a, b):
        """Steps from inside room a to a door of room b (1 step to exit)."""
        if a == b:
            return 0
        if self.passages.get(a) == b:
            return 0
        d = self.dist_to_room(b)
        best = None
        for door in self.room_doors.get(a, []):
            if door in d:
                v = d[door] + 1
                if best is None or v < best:
                    best = v
        return best

    # -- this turn's movement ---------------------------------------------
    def first_cells(self, origin, blocked):
        """Possible first path cells. origin = ('corridor', cell) or ('room', id)."""
        kind, where = origin
        if kind == "room":
            return [d for d in self.room_doors.get(where, []) if d not in blocked and d not in self.starts]
        return [nb for nb in self.neighbors(where) if nb not in blocked and nb not in self.starts]

    def room_entries(self, origin, steps, blocked):
        """{room: path} for rooms enterable this turn with at most `steps` squares."""
        kind, where = origin
        out = {}
        if kind == "corridor":
            for room in self.door_rooms.get(where, []):
                out[room] = []
        starts = self.first_cells(origin, blocked)
        # BFS over simple shortest paths; door squares terminate.
        prev = {}
        dist = {}
        q = deque()
        for s in starts:
            if s not in dist:
                dist[s] = 1
                prev[s] = None
                q.append(s)
        origin_cell = where if kind == "corridor" else None
        while q:
            cur = q.popleft()
            if cur in self.door_rooms and not (kind == "room" and cur in self.room_doors.get(where, [])):
                for room in self.door_rooms[cur]:
                    if kind == "room" and room == where:
                        continue
                    if room not in out:
                        out[room] = self._unwind(prev, cur)
                continue  # never walk through another door
            if dist[cur] >= steps:
                continue
            for nb in self.neighbors(cur):
                if nb in dist or nb in blocked or nb in self.starts or nb == origin_cell:
                    continue
                dist[nb] = dist[cur] + 1
                prev[nb] = cur
                q.append(nb)
        return out

    @staticmethod
    def _unwind(prev, cell):
        path = []
        while cell is not None:
            path.append(cell)
            cell = prev[cell]
        path.reverse()
        return path

    def corridor_targets(self, origin, steps, blocked):
        """Cells that might be reachable with exactly `steps` squares (parity/distance filter)."""
        kind, where = origin
        starts = self.first_cells(origin, blocked)
        own_doors = set(self.room_doors.get(where, [])) if kind == "room" else set()
        dist = {}
        q = deque()
        for s in starts:
            dist[s] = 1
            q.append(s)
        origin_cell = where if kind == "corridor" else None
        while q:
            cur = q.popleft()
            if cur in self.door_rooms and cur not in own_doors:
                continue
            if dist[cur] >= steps:
                continue
            for nb in self.neighbors(cur):
                if nb in dist or nb in blocked or nb in self.starts or nb == origin_cell:
                    continue
                dist[nb] = dist[cur] + 1
                q.append(nb)
        return [c for c, d in dist.items()
                if (steps - d) % 2 == 0 and c not in self.door_rooms]

    def exact_path(self, origin, steps, target, blocked, node_limit=30000):
        """Simple path of exactly `steps` squares ending on target, or None."""
        kind, where = origin
        own_doors = set(self.room_doors.get(where, [])) if kind == "room" else set()
        to_target = self.bfs([target], blocked=blocked | ({where} if kind == "corridor" else set()))
        origin_cell = where if kind == "corridor" else None
        nodes = [0]
        path = []
        on_path = set()

        def ok(cell):
            if cell in on_path or cell in blocked or cell in self.starts or cell == origin_cell:
                return False
            if cell in self.door_rooms and cell not in own_doors:
                return False
            return True

        def rec(cell, left):
            nodes[0] += 1
            if nodes[0] > node_limit:
                return False
            if left == 0:
                return cell == target
            d = to_target.get(cell)
            if d is None or d > left or (left - d) % 2:
                return False
            nbs = [nb for nb in self.neighbors(cell) if ok(nb)]
            nbs.sort(key=lambda nb: -(to_target.get(nb, -99)))  # wander first, home in late
            for nb in nbs:
                path.append(nb)
                on_path.add(nb)
                if rec(nb, left - 1):
                    return True
                path.pop()
                on_path.discard(nb)
            return False

        for first in self.first_cells(origin, blocked):
            if first in self.door_rooms and first not in own_doors:
                continue
            path[:] = [first]
            on_path.clear()
            on_path.add(first)
            if rec(first, steps - 1):
                return list(path)
        return None

    def validate(self, origin, path, steps, blocked, enter_room=None):
        """Independent legality check of a planned move."""
        kind, where = origin
        if enter_room is None and len(path) != steps:
            return False
        if len(path) > steps:
            return False
        seen = set()
        prev = where if kind == "corridor" else None
        own_doors = set(self.room_doors.get(where, [])) if kind == "room" else set()
        for idx, cell in enumerate(path):
            if cell not in self.walk or cell in blocked or cell in self.starts or cell in seen:
                return False
            if prev is None:
                if cell not in own_doors:
                    return False
            elif abs(prev[0] - cell[0]) + abs(prev[1] - cell[1]) != 1:
                return False
            last = idx == len(path) - 1
            if cell in self.door_rooms and not last and not (idx == 0 and cell in own_doors):
                return False
            seen.add(cell)
            prev = cell
        if enter_room is not None:
            end = path[-1] if path else (where if kind == "corridor" else None)
            if end is None or enter_room not in self.door_rooms.get(end, []):
                return False
            if kind == "room" and enter_room == where:
                return False
        else:
            if path and path[-1] in self.door_rooms:
                return False
        return True
