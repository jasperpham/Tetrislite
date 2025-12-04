import sys
import random
import math
import array
import pygame

# -------------------- CONFIG --------------------

GRID_WIDTH = 10
GRID_HEIGHT = 20
BLOCK_SIZE = 30

PLAYFIELD_WIDTH = GRID_WIDTH * BLOCK_SIZE
PLAYFIELD_HEIGHT = GRID_HEIGHT * BLOCK_SIZE

WINDOW_WIDTH = 1150
WINDOW_HEIGHT = 720


FPS = 60

DEFAULT_CONTROLS = {
    "move_left": pygame.K_LEFT,
    "move_right": pygame.K_RIGHT,
    "soft_drop": pygame.K_DOWN,
    "hard_drop": pygame.K_SPACE,
    "rotate": pygame.K_UP,
    "hold": pygame.K_LSHIFT,
    "pause": pygame.K_p,
}

BLACK = (0, 0, 0)
DARK_GREY = (0, 15, 5)
GREY = (0, 60, 20)
WHITE = (0, 255, 120)
RED = (0, 180, 80)
GREEN = (0, 200, 100)
YELLOW = (120, 255, 120)
OUTLINE_COLOR = (0, 120, 60)

PIECE_COLOR = (0, 255, 120)
GHOST_COLOR = (0, 160, 80)

# VS mode rendering cell size (smaller so 2 boards fit)
VS_BLOCK_SIZE = 22

# Simple modern-style attack table (lines cleared -> garbage sent)
ATTACK_TABLE = {
    1: 1,  # single sends 1
    2: 2,
    3: 3,
    4: 4,  # tetris sends 4
}


# -------------------- SHAPES --------------------

BASE_SHAPES = {
    "I": ["....",
          "....",
          "####",
          "...."],
    "O": ["....",
          ".##.",
          ".##.",
          "...."],
    "T": ["....",
          ".###",
          "..#.",
          "...."],
    "S": ["....",
          "..##",
          ".##.",
          "...."],
    "Z": ["....",
          ".##.",
          "..##.",
          "...."],
    "J": ["....",
          ".###",
          ".#..",
          "...."],
    "L": ["....",
          ".###",
          "...#",
          "...."],
}

# Fix the typo in Z shape
BASE_SHAPES["Z"] = ["....",
                    ".##.",
                    "..##",
                    "...."]

SHAPE_COLORS = {name: PIECE_COLOR for name in BASE_SHAPES.keys()}


def rotate_grid_90(grid):
    size = len(grid)
    rotated = []
    for x in range(size):
        row = ""
        for y in range(size - 1, -1, -1):
            row += grid[y][x]
        rotated.append(row)
    return rotated


def build_rotations():
    rots = {}
    for name, grid in BASE_SHAPES.items():
        current = grid
        arr = []
        for _ in range(4):
            arr.append(current)
            current = rotate_grid_90(current)
        rots[name] = arr
    return rots


ROTATIONS = build_rotations()
PIECE_TYPES = list(BASE_SHAPES.keys())

# -------------------- ABILITIES --------------------

ABILITY_DEFS = [
    {
        "id": "clear4",
        "name": "Purge Cycle",
        "desc": "Every 120s: clear up to 4 lowest non-empty lines.",
        "cooldown": 120.0,
    },
    {
        "id": "double_hold",
        "name": "Double Buffer",
        "desc": "Second hold key using an extra hold slot.",
        "cooldown": 0.0,
    },
    {
        "id": "bomb",
        "name": "Smart Bomb",
        "desc": "Crater: 5x5 circular blast in the center, then gravity.",
        "cooldown": 90.0,
    },
]

# -------------------- SOUND HELPERS --------------------


def create_tone(frequency, duration_ms, volume=0.4, sample_rate=44100):
    n_samples = int(sample_rate * duration_ms / 1000)
    buf = array.array("h")
    amplitude = int(32767 * volume)
    for i in range(n_samples):
        t = i / sample_rate
        sample = int(amplitude * math.sin(2 * math.pi * frequency * t))
        buf.append(sample)
    return pygame.mixer.Sound(buffer=buf)


def create_melody(frequencies, note_ms=120, gap_ms=20,
                  volume=0.4, sample_rate=44100):
    buf = array.array("h")
    amp = int(32767 * volume)
    for f in frequencies:
        n_note = int(sample_rate * note_ms / 1000)
        n_gap = int(sample_rate * gap_ms / 1000)
        for i in range(n_note):
            t = i / sample_rate
            val = int(amp * math.sin(2 * math.pi * f * t))
            buf.append(val)
        for _ in range(n_gap):
            buf.append(0)
    return pygame.mixer.Sound(buffer=buf)

# -------------------- GAME LOGIC --------------------


class Tetromino:
    def __init__(self, name):
        self.name = name
        self.rotation = 0
        self.x = GRID_WIDTH // 2 - 2
        self.y = -2
        self.color = SHAPE_COLORS[name]

    @property
    def shape(self):
        return ROTATIONS[self.name][self.rotation]


class TetrisGame:
    def __init__(self, mode, controls, sounds, speed_settings):
        self.mode = mode  # "sprint", "endless", "lite", "vs" (we'll use "endless" in VS driver)
        self.controls = controls
        self.sounds = sounds or {}

        self.grid = [[None for _ in range(GRID_WIDTH)]
                     for _ in range(GRID_HEIGHT)]
        self.current_piece = self.new_piece()
        self.next_piece = self.new_piece()

        # hold system → supports up to 2 slots
        self.hold_slots = [None]   # slot 0 always exists
        self.active_hold_index = 0
        self.hold2_unlocked = False
        self.hold_used = False     # once per piece

        self.lines_cleared = 0
        self.game_over = False
        self.win = False
        self.message = ""

        self.fall_timer = 0.0
        self.elapsed_time = 0.0

        self.das = max(0.0, speed_settings.get("das_ms", 160) / 1000.0)
        self.arr = max(0.01, speed_settings.get("arr_ms", 40) / 1000.0)

        self.input_time = 0.0
        self.left_held = False
        self.right_held = False
        self.left_press_time = 0.0
        self.right_press_time = 0.0
        self.left_last_repeat = 0.0
        self.right_last_repeat = 0.0

        self.paused = False

        self.last_clear_time = None
        self.clear_streak = 0
        self.clear_notes_count = self.sounds.get("_clear_count", 0)

        # soft drop accel + lock delay
        self.soft_drop_hold = 0.0
        self.lock_delay = 0.4
        self.lock_timer = 0.0
        self.on_ground = False

        # drop impact
        self.impact_duration = 0.08
        self.impact_timer = 0.0

        # line-clear green flashes
        self.clear_flash_interval = 0.08  # on/off period
        self.clear_flash_count = 0        # 2 or 3 flash pairs
        self.clear_flash_elapsed = 0.0

        # ability system (lite mode)
        self.abilities = []          # {id,name,key,cooldown,last_use,...}
        self.max_abilities = 3
        self.next_ability_lines = 20
        self.pending_ability_choice = False

        # VS-related: outgoing attack and garbage queue
        self.attack_outgoing = 0
        self.garbage_incoming = 0

        # Single-use item system (bomb / drill / wave / robot)
        self.item = None          # "bomb", "drill", "wave", "robot"
        self.item_uses_left = 0   # usually 1




    def reset(self):
        self.grid = [[None for _ in range(GRID_WIDTH)]
                     for _ in range(GRID_HEIGHT)]
        self.current_piece = self.new_piece()
        self.next_piece = self.new_piece()

        self.hold_slots = [None]
        self.active_hold_index = 0
        self.hold2_unlocked = False
        self.hold_used = False

        self.lines_cleared = 0
        self.game_over = False
        self.win = False
        self.message = ""
        self.fall_timer = 0.0
        self.elapsed_time = 0.0
        self.input_time = 0.0
        self.left_held = False
        self.right_held = False
        self.last_clear_time = None
        self.clear_streak = 0
        self.soft_drop_hold = 0.0
        self.lock_timer = 0.0
        self.on_ground = False
        self.impact_timer = 0.0
        self.clear_flash_count = 0
        self.clear_flash_elapsed = 0.0

        self.abilities = []
        self.next_ability_lines = 20
        self.pending_ability_choice = False

        self.attack_outgoing = 0
        self.garbage_incoming = 0

        self.item = None
        self.item_uses_left = 0


    def new_piece(self):
        return Tetromino(random.choice(PIECE_TYPES))

    def spawn_piece_center(self, name):
        p = Tetromino(name)
        p.x = GRID_WIDTH // 2 - 2
        p.y = -2
        return p

    def get_level(self):
        return self.lines_cleared // 10

    def get_fall_interval(self, soft_drop_pressed):
        if self.mode == "sprint":
            base = 0.6
        else:
            level = self.get_level()
            base = max(0.12, 0.8 - level * 0.06)

        if soft_drop_pressed:
            hold = min(self.soft_drop_hold, 0.8)
            ratio = hold / 0.8
            factor = 0.4 - 0.28 * ratio
            factor = max(0.12, factor)
            return base * factor
        else:
            return base

    def current_shape(self):
        return self.current_piece.shape

    def check_collision(self, shape, x, y):
        for r in range(4):
            for c in range(4):
                if shape[r][c] == "#":
                    gx = x + c
                    gy = y + r
                    if gx < 0 or gx >= GRID_WIDTH:
                        return True
                    if gy >= GRID_HEIGHT:
                        return True
                    if gy >= 0 and self.grid[gy][gx] is not None:
                        return True
        return False

    def move_piece(self, dx):
        if self.game_over or self.paused:
            return
        new_x = self.current_piece.x + dx
        if not self.check_collision(self.current_shape(), new_x,
                                    self.current_piece.y):
            self.current_piece.x = new_x
            if self.on_ground:
                self.lock_timer = 0.0
            snd = self.sounds.get("move")
            if snd:
                snd.play()

    def rotate_piece(self):
        if self.game_over or self.paused:
            return
        new_rotation = (self.current_piece.rotation + 1) % 4
        shape = ROTATIONS[self.current_piece.name][new_rotation]
        for dx in (0, -1, 1):
            nx = self.current_piece.x + dx
            if not self.check_collision(shape, nx, self.current_piece.y):
                self.current_piece.rotation = new_rotation
                self.current_piece.x = nx
                if self.on_ground:
                    self.lock_timer = 0.0
                snd = self.sounds.get("move")
                if snd:
                    snd.play()
                return

    def hard_drop(self):
        if self.game_over or self.paused:
            return
        while not self.check_collision(
                self.current_shape(),
                self.current_piece.x,
                self.current_piece.y + 1):
            self.current_piece.y += 1
        self.on_ground = False
        self.lock_timer = 0.0
        snd = self.sounds.get("drop")
        if snd:
            snd.play()
        self.lock_piece()

    def step_down(self):
        if self.game_over or self.paused:
            return
        new_y = self.current_piece.y + 1
        if self.check_collision(self.current_shape(),
                                self.current_piece.x, new_y):
            return
        self.current_piece.y = new_y

    def handle_line_clear_effects(self, cleared):
        if cleared <= 0:
            return

        # track lines before / after so we can trigger item drops
        prev_lines = self.lines_cleared
        self.lines_cleared += cleared
        new_total = self.lines_cleared

        # VS attack calculation (used by VS driver)
        attack = ATTACK_TABLE.get(cleared, 0)
        self.attack_outgoing += attack

        # line clear flash pattern (green flashes)
        self.clear_flash_elapsed = 0.0
        self.clear_flash_count = 2 if cleared < 4 else 3

        # tetris jingle
        if cleared == 4:
            snd_t = self.sounds.get("tetris")
            if snd_t:
                snd_t.play()

        # combo beep streak
        if self.clear_notes_count > 0:
            now = self.elapsed_time
            if self.last_clear_time is None or now - self.last_clear_time > 8.0:
                self.clear_streak = 0
            else:
                self.clear_streak += 1
            self.last_clear_time = now
            idx = min(self.clear_streak, self.clear_notes_count - 1)
            snd = self.sounds.get(f"clear_{idx}")
            if snd:
                snd.play()

        # ability thresholds for lite mode only
        if self.mode == "lite" and not self.game_over:
            if (len(self.abilities) < self.max_abilities and
                    self.lines_cleared >= self.next_ability_lines):
                self.next_ability_lines += 20
                self.pending_ability_choice = True
                self.paused = True

        # --- single-use item drops (bound to E) ---
        # get an item on a Tetris or every 10 total lines
        should_award = (cleared == 4)
        if not should_award:
            if prev_lines // 10 < new_total // 10:
                should_award = True

        if should_award:
            self.award_random_item()


    def lock_piece(self):
        # impact trigger on any lock

        shape = self.current_shape()
        for r in range(4):
            for c in range(4):
                if shape[r][c] == "#":
                    gx = self.current_piece.x + c
                    gy = self.current_piece.y + r
                    if gy < 0:
                        self.game_over = True
                        self.win = False
                        self.message = "Top out!"
                        snd = self.sounds.get("game_over")
                        if snd:
                            snd.play()
                        return
                    if 0 <= gy < GRID_HEIGHT and 0 <= gx < GRID_WIDTH:
                        self.grid[gy][gx] = self.current_piece.color

        cleared = self.clear_lines()
        self.handle_line_clear_effects(cleared)

        self.hold_used = False

        if self.mode == "sprint" and self.lines_cleared >= 100:
            self.game_over = True
            self.win = True
            self.message = "100 lines cleared!"
            snd = self.sounds.get("victory")
            if snd:
                snd.play()
            return

        self.current_piece = self.next_piece
        self.current_piece.x = GRID_WIDTH // 2 - 2
        self.current_piece.y = -2
        self.next_piece = self.new_piece()

        if self.check_collision(self.current_shape(),
                                self.current_piece.x,
                                self.current_piece.y):
            self.game_over = True
            self.win = False
            self.message = "Top out!"
            snd = self.sounds.get("game_over")
            if snd:
                snd.play()

    def clear_lines(self):
        new_grid = []
        cleared = 0
        for row in self.grid:
            if all(cell is not None for cell in row):
                cleared += 1
            else:
                new_grid.append(row)
        while len(new_grid) < GRID_HEIGHT:
            new_grid.insert(0, [None for _ in range(GRID_WIDTH)])
        self.grid = new_grid
        return cleared

    def get_ghost_y(self):
        y = self.current_piece.y
        x = self.current_piece.x
        while not self.check_collision(self.current_shape(), x, y + 1):
            y += 1
        return y

    def hold_current(self, slot_index=None):
        if self.game_over or self.paused:
            return
        if self.hold_used:
            return

        if slot_index is None:
            idx = self.active_hold_index
        else:
            idx = slot_index
            if idx < 0 or idx >= len(self.hold_slots):
                return

        current_name = self.current_piece.name
        slot_piece = self.hold_slots[idx]

        if slot_piece is None:
            self.hold_slots[idx] = current_name
            self.current_piece = self.next_piece
            self.current_piece.x = GRID_WIDTH // 2 - 2
            self.current_piece.y = -2
            self.next_piece = self.new_piece()
        else:
            self.current_piece = self.spawn_piece_center(slot_piece)
            self.hold_slots[idx] = current_name

        self.hold_used = True

        if self.check_collision(self.current_shape(),
                                self.current_piece.x,
                                self.current_piece.y):
            self.game_over = True
            self.win = False
            self.message = "Top out!"
            snd = self.sounds.get("game_over")
            if snd:
                snd.play()

    # ------------- ABILITY HELPERS -------------

    def add_ability(self, ability_def, bind_key):
        if len(self.abilities) >= self.max_abilities:
            return
        ability = {
            "id": ability_def["id"],
            "name": ability_def["name"],
            "desc": ability_def["desc"],
            "key": bind_key,
            "cooldown": ability_def["cooldown"],
            "last_use": None,
        }
        self.abilities.append(ability)

        # Double Buffer unlocks second hold slot, but key acts like 2nd Shift
        if ability["id"] == "double_hold":
            if not self.hold2_unlocked:
                self.hold2_unlocked = True
                if len(self.hold_slots) < 2:
                    self.hold_slots.append(None)

    def use_ability(self, ability):
        if self.game_over or self.paused:
            return

        now = self.elapsed_time
        cd = ability.get("cooldown", 0.0)
        last = ability.get("last_use")

        if cd > 0.0 and last is not None and (now - last) < cd:
            return

        aid = ability["id"]

        if aid == "clear4":
            used = self.ability_clear4()
        elif aid == "double_hold":
            used = self.ability_second_hold()
        elif aid == "bomb":
            used = self.ability_bomb()
        else:
            used = False

        if used:
            ability["last_use"] = now

    def ability_clear4(self):
        lines_to_clear = []
        for row in range(GRID_HEIGHT - 1, -1, -1):
            if any(self.grid[row][x] is not None for x in range(GRID_WIDTH)):
                lines_to_clear.append(row)
                if len(lines_to_clear) == 4:
                    break
        if not lines_to_clear:
            return False

        mask = set(lines_to_clear)
        new_grid = []
        for r in range(GRID_HEIGHT):
            if r not in mask:
                new_grid.append(self.grid[r])
        while len(new_grid) < GRID_HEIGHT:
            new_grid.insert(0, [None for _ in range(GRID_WIDTH)])
        self.grid = new_grid

        cleared = len(lines_to_clear)
        self.handle_line_clear_effects(cleared)
        return True

    def ability_second_hold(self):
        """Use extra hold slot as a second Shift."""
        if not self.hold2_unlocked or len(self.hold_slots) < 2:
            return False
        before = self.hold_used
        self.hold_current(slot_index=1)
        # Only count as 'used' if we actually did a hold this piece
        return (not before) and self.hold_used

    def ability_bomb(self):
        cx = GRID_WIDTH // 2
        cy = GRID_HEIGHT // 2
        radius_sq = (2.5 ** 2)

        any_hit = False
        for y in range(GRID_HEIGHT):
            for x in range(GRID_WIDTH):
                dx = x - cx
                dy = y - cy
                if dx * dx + dy * dy <= radius_sq:
                    if self.grid[y][x] is not None:
                        any_hit = True
                    self.grid[y][x] = None

        if not any_hit:
            return False

        # gravity
        for x in range(GRID_WIDTH):
            stack = [self.grid[y][x] for y in range(GRID_HEIGHT)
                     if self.grid[y][x] is not None]
            for y in range(GRID_HEIGHT - 1, -1, -1):
                if stack:
                    self.grid[y][x] = stack.pop()
                else:
                    self.grid[y][x] = None

        cleared = self.clear_lines()
        self.handle_line_clear_effects(cleared)
        return True

    # ------------- ITEM SYSTEM (E-KEY POWERUPS) -------------

    def award_random_item(self):
        """Give a random single-use item if we don't already have one."""
        if self.item is not None:
            return
        self.item = random.choice(["bomb", "drill", "wave", "robot"])
        self.item_uses_left = 1

    def use_item(self):
        """Activate the currently held item (triggered by the E key)."""
        if self.game_over or self.paused:
            return
        if self.item is None or self.item_uses_left <= 0:
            return

        used = False
        if self.item == "bomb":
            used = self.item_bomb()
        elif self.item == "drill":
            used = self.item_drill()
        elif self.item == "wave":
            used = self.item_wave()
        elif self.item == "robot":
            used = self.item_robot()

        if used:
            self.item_uses_left -= 1
            if self.item_uses_left <= 0:
                self.item = None

    def item_bomb(self):
        """Circular crater in the middle of the field."""
        # just reuse the old bomb ability logic
        return self.ability_bomb()

    def item_wave(self):
        """Clear the bottom 5 rows and drop everything above."""
        start_row = max(0, GRID_HEIGHT - 5)
        # wipe bottom 5 rows
        for y in range(start_row, GRID_HEIGHT):
            for x in range(GRID_WIDTH):
                self.grid[y][x] = None

        # gravity on all columns
        for x in range(GRID_WIDTH):
            stack = [self.grid[y][x] for y in range(GRID_HEIGHT)
                     if self.grid[y][x] is not None]
            for y in range(GRID_HEIGHT - 1, -1, -1):
                self.grid[y][x] = stack.pop() if stack else None

        # count it as 5 cleared lines for combos / item thresholds
        self.handle_line_clear_effects(5)
        return True

    def item_drill(self):
        """Drill a 2-wide vertical tunnel straight down."""
        # choose 2-wide window roughly under the current piece
        cx = max(0, min(self.current_piece.x + 1, GRID_WIDTH - 2))

        # carve the tunnel
        for y in range(GRID_HEIGHT):
            self.grid[y][cx] = None
            self.grid[y][cx + 1] = None

        # gravity in just those two columns
        for col in (cx, cx + 1):
            stack = [self.grid[y][col] for y in range(GRID_HEIGHT)
                     if self.grid[y][col] is not None]
            for y in range(GRID_HEIGHT - 1, -1, -1):
                self.grid[y][col] = stack.pop() if stack else None

        cleared = self.clear_lines()
        self.handle_line_clear_effects(cleared)
        return True

    # --- helpers used by the robot item ---

    def _collision_on_grid(self, shape, x, y, grid):
        """Collision test against an arbitrary grid (used by robot item)."""
        for r in range(4):
            for c in range(4):
                if shape[r][c] != "#":
                    continue
                gx = x + c
                gy = y + r
                if gx < 0 or gx >= GRID_WIDTH:
                    return True
                if gy >= GRID_HEIGHT:
                    return True
                if gy >= 0 and grid[gy][gx] is not None:
                    return True
        return False

    def _evaluate_position(self, piece_name, rotation, x):
        """Heuristic score for dropping a piece at (rotation, x). Higher is better."""
        shape = ROTATIONS[piece_name][rotation]
        grid_copy = [row[:] for row in self.grid]

        # find landing row
        y = -2
        while True:
            if self._collision_on_grid(shape, x, y + 1, grid_copy):
                break
            y += 1
            if y > GRID_HEIGHT:
                break

        if self._collision_on_grid(shape, x, y, grid_copy):
            return None

        # lock into copy
        for r in range(4):
            for c in range(4):
                if shape[r][c] != "#":
                    continue
                gx = x + c
                gy = y + r
                if gy < 0:
                    return None  # would top out
                grid_copy[gy][gx] = PIECE_COLOR

        # clear full lines
        lines_cleared = 0
        new_grid = []
        for row in grid_copy:
            if all(cell is not None for cell in row):
                lines_cleared += 1
            else:
                new_grid.append(row)
        while len(new_grid) < GRID_HEIGHT:
            new_grid.insert(0, [None for _ in range(GRID_WIDTH)])

        # simple features: max height + holes
        col_heights = [0] * GRID_WIDTH
        holes = 0
        for x in range(GRID_WIDTH):
            block_seen = False
            for y in range(GRID_HEIGHT):
                if new_grid[y][x] is not None:
                    if not block_seen:
                        block_seen = True
                        col_heights[x] = GRID_HEIGHT - y
                elif block_seen:
                    holes += 1
        max_height = max(col_heights) if col_heights else 0

        # score: reward lines, punish height & holes
        score = lines

    # ------------- GARBAGE (VS) -------------

    def apply_garbage(self, lines):
        """Apply 'lines' of garbage from bottom, with one random hole each."""
        if lines <= 0 or self.game_over:
            return
        for _ in range(lines):
            # shift everything up by one
            for y in range(GRID_HEIGHT - 1):
                self.grid[y] = self.grid[y + 1][:]

            # new garbage row on bottom
            hole = random.randint(0, GRID_WIDTH - 1)
            row = [None if x == hole else PIECE_COLOR for x in range(GRID_WIDTH)]
            self.grid[GRID_HEIGHT - 1] = row

            # move active piece up to keep relative spacing
            self.current_piece.y -= 1

        # top-out check after garbage
        for x in range(GRID_WIDTH):
            if self.grid[0][x] is not None and self.current_piece.y <= 0:
                self.game_over = True
                self.win = False
                self.message = "Garbage overflow"
                snd = self.sounds.get("game_over")
                if snd:
                    snd.play()
                break

    # --------------------------------------------

    def update_horizontal_auto_shift(self):
        if self.game_over or self.paused:
            return

        if self.left_held and not self.right_held:
            dt_press = self.input_time - self.left_press_time
            if dt_press >= self.das:
                if self.input_time - self.left_last_repeat >= self.arr:
                    self.left_last_repeat = self.input_time
                    self.move_piece(-1)
        elif self.right_held and not self.left_held:
            dt_press = self.input_time - self.right_press_time
            if dt_press >= self.das:
                if self.input_time - self.right_last_repeat >= self.arr:
                    self.right_last_repeat = self.input_time
                    self.move_piece(1)

    def is_clear_flash_active(self):
        """Return True if we are currently in a green-flash phase."""
        if self.clear_flash_count <= 0:
            return False
        if self.clear_flash_interval <= 0:
            return False
        phase = int(self.clear_flash_elapsed / self.clear_flash_interval)
        total_phases = self.clear_flash_count * 2  # on/off phases
        if phase >= total_phases:
            return False
        return (phase % 2) == 0  # even = flashing on

    def update(self, dt, key_state, events):
        if not self.game_over and not self.paused:
            self.elapsed_time += dt
            self.input_time += dt

        # decay impact
        if self.impact_timer > 0.0:
            self.impact_timer = max(0.0, self.impact_timer - dt)

        # advance line-clear flashes
        if self.clear_flash_count > 0:
            self.clear_flash_elapsed += dt
            total_phases = self.clear_flash_count * 2
            if self.clear_flash_elapsed >= total_phases * self.clear_flash_interval:
                self.clear_flash_count = 0
                self.clear_flash_elapsed = 0.0

        for ev in events:
            if ev.type == pygame.KEYDOWN:
                if ev.key == self.controls["pause"]:
                    self.paused = not self.paused

                # ability hotkeys (like extra Shift for double_hold)
                if not self.game_over and not self.paused:
                    for ability in self.abilities:
                        if ev.key == ability["key"]:
                            self.use_ability(ability)
                            break

                if self.game_over or self.paused:
                    continue

                if ev.key == self.controls["move_left"]:
                    self.left_held = True
                    self.left_press_time = self.input_time
                    self.left_last_repeat = self.input_time
                    self.move_piece(-1)
                elif ev.key == self.controls["move_right"]:
                    self.right_held = True
                    self.right_press_time = self.input_time
                    self.right_last_repeat = self.input_time
                    self.move_piece(1)
                elif ev.key == self.controls["rotate"]:
                    self.rotate_piece()
                elif ev.key == self.controls["hard_drop"]:
                    self.hard_drop()
                elif ev.key == self.controls["hold"]:
                    self.hold_current()

            elif ev.type == pygame.KEYUP:
                if ev.key == self.controls["move_left"]:
                    self.left_held = False
                elif ev.key == self.controls["move_right"]:
                    self.right_held = False

        if self.game_over or self.paused:
            return

        self.update_horizontal_auto_shift()

        soft_down = key_state[self.controls["soft_drop"]] \
            if self.controls["soft_drop"] is not None else False

        if soft_down:
            self.soft_drop_hold += dt
        else:
            self.soft_drop_hold = 0.0

        fall_interval = self.get_fall_interval(soft_down)

        self.fall_timer += dt
        if self.fall_timer >= fall_interval:
            self.fall_timer = 0.0
            self.step_down()

        if self.check_collision(self.current_shape(),
                                self.current_piece.x,
                                self.current_piece.y + 1):
            if not self.on_ground:
                self.on_ground = True
                self.lock_timer = 0.0
            self.lock_timer += dt
            if self.lock_timer >= self.lock_delay:
                self.on_ground = False
                self.lock_timer = 0.0
                self.lock_piece()
        else:
            self.on_ground = False
            self.lock_timer = 0.0

# -------------------- DRAWING --------------------


def draw_piece_preview(surface, piece_name, x_offset, y_offset):
    shape = ROTATIONS[piece_name][0]
    color = SHAPE_COLORS[piece_name]
    size = int(BLOCK_SIZE // 1.5)
    for r in range(4):
        for c in range(4):
            if shape[r][c] == "#":
                bx = x_offset + c * size
                by = y_offset + r * size
                rct = pygame.Rect(bx, by, size, size)
                pygame.draw.rect(surface, color, rct)
                pygame.draw.rect(surface, OUTLINE_COLOR, rct, 1)

def draw_piece_preview_small(surface, piece_name, x_offset, y_offset, cell_size):
    shape = ROTATIONS[piece_name][0]
    color = SHAPE_COLORS[piece_name]
    for r in range(4):
        for c in range(4):
            if shape[r][c] == "#":
                bx = x_offset + c * cell_size
                by = y_offset + r * cell_size
                rct = pygame.Rect(bx, by, cell_size, cell_size)
                pygame.draw.rect(surface, color, rct)
                pygame.draw.rect(surface, OUTLINE_COLOR, rct, 1)

def draw_grid(surface, game, font, mode):
    field_rect = pygame.Rect(40, 40, PLAYFIELD_WIDTH, PLAYFIELD_HEIGHT)
    pygame.draw.rect(surface, DARK_GREY, field_rect)
    pygame.draw.rect(surface, OUTLINE_COLOR, field_rect, 3)

    # settled blocks
    for y in range(GRID_HEIGHT):
        for x in range(GRID_WIDTH):
            color = game.grid[y][x]
            if color is not None:
                bx = 40 + x * BLOCK_SIZE
                by = 40 + y * BLOCK_SIZE
                r = pygame.Rect(bx, by, BLOCK_SIZE, BLOCK_SIZE)
                pygame.draw.rect(surface, color, r)
                pygame.draw.rect(surface, OUTLINE_COLOR, r, 1)

    # ghost piece
    ghost_y = game.get_ghost_y()
    piece = game.current_piece
    shape = piece.shape
    for r in range(4):
        for c in range(4):
            if shape[r][c] == "#":
                gx = piece.x + c
                gy = ghost_y + r
                if gy < 0:
                    continue
                if 0 <= gy < GRID_HEIGHT and game.grid[gy][gx] is None:
                    bx = 40 + gx * BLOCK_SIZE
                    by = 40 + gy * BLOCK_SIZE
                    rct = pygame.Rect(bx, by, BLOCK_SIZE, BLOCK_SIZE)
                    pygame.draw.rect(surface, GHOST_COLOR, rct)
                    pygame.draw.rect(surface, OUTLINE_COLOR, rct, 1)

    # current falling piece
    for r in range(4):
        for c in range(4):
            if shape[r][c] == "#":
                gx = piece.x + c
                gy = piece.y + r
                if gy < 0:
                    continue
                bx = 40 + gx * BLOCK_SIZE
                by = 40 + gy * BLOCK_SIZE
                rct = pygame.Rect(bx, by, BLOCK_SIZE, BLOCK_SIZE)
                pygame.draw.rect(surface, piece.color, rct)
                pygame.draw.rect(surface, OUTLINE_COLOR, rct, 1)

    # grid lines
    for x in range(GRID_WIDTH + 1):
        sx = 40 + x * BLOCK_SIZE
        pygame.draw.line(surface, GREY, (sx, 40),
                         (sx, 40 + PLAYFIELD_HEIGHT))
    for y in range(GRID_HEIGHT + 1):
        sy = 40 + y * BLOCK_SIZE
        pygame.draw.line(surface, GREY, (40, sy),
                         (40 + PLAYFIELD_WIDTH, sy))



    # side panel
    side_x = 40 + PLAYFIELD_WIDTH + 30
    pygame.draw.rect(surface, BLACK,
                     pygame.Rect(side_x - 10, 40, 200, PLAYFIELD_HEIGHT))

    label = font.render(f"Mode: {mode.capitalize()}", True, WHITE)
    surface.blit(label, (side_x, 50))

    lines_label = font.render(f"Lines: {game.lines_cleared}", True, WHITE)
    surface.blit(lines_label, (side_x, 90))

    if mode == "sprint":
        time_str = f"Time: {game.elapsed_time:6.2f}s"
        time_label = font.render(time_str, True, WHITE)
        surface.blit(time_label, (side_x, 130))
        left_label = font.render(f"Left: {max(0, 100 - game.lines_cleared)}",
                                 True, WHITE)
        surface.blit(left_label, (side_x, 160))
    else:
        level = game.get_level()
        lvl_label = font.render(f"Level: {level}", True, WHITE)
        surface.blit(lvl_label, (side_x, 130))

    np_label = font.render("Next:", True, WHITE)
    surface.blit(np_label, (side_x, 210))
    draw_piece_preview(surface, game.next_piece.name, side_x, 240)

    # hold display (up to 2 slots)
    hold_label = font.render("Hold:", True, WHITE)
    surface.blit(hold_label, (side_x, 320))
    if game.hold_slots[0] is not None:
        draw_piece_preview(surface, game.hold_slots[0], side_x, 350)
    if game.hold2_unlocked and len(game.hold_slots) > 1:
        label2 = font.render("[slot 2]", True, GREY)
        surface.blit(label2, (side_x, 430))
        if game.hold_slots[1] is not None:
            draw_piece_preview(surface, game.hold_slots[1], side_x + 40, 430)

    if game.paused and not game.game_over:
        pause_label = font.render("PAUSED", True, YELLOW)
        surface.blit(pause_label, (side_x, 500))

    # abilities hint (lite)
    if mode == "lite" and game.abilities:
        ab_y = 540
        surface.blit(font.render("Abilities:", True, WHITE),
                     (side_x, ab_y))
        for i, ability in enumerate(game.abilities[:3]):
            key_name_str = pygame.key.name(ability["key"])
            txt = f"[{key_name_str}] {ability['name']}"
            surface.blit(font.render(txt, True, GREY),
                         (side_x, ab_y + 24 * (i + 1)))

    # ----- global GREEN flash on line clear -----
    if game.is_clear_flash_active():
        w, h = surface.get_size()
        flash = pygame.Surface((w, h), pygame.SRCALPHA)
        flash.fill((0, 255, 120, 120))  # semi-transparent green
        surface.blit(flash, (0, 0), special_flags=pygame.BLEND_ADD)


def draw_vs_board(surface, game, font, label_text, origin_x, origin_y):
    """Board renderer for VS mode (smaller size, side-by-side)."""
    cell = VS_BLOCK_SIZE
    field_width = GRID_WIDTH * cell
    field_height = GRID_HEIGHT * cell

    # title + stats ABOVE board so they don't bleed into the box
    title = font.render(label_text, True, WHITE)
    surface.blit(title, (origin_x, origin_y - 36))
    lines_label = font.render(f"Lines: {game.lines_cleared}", True, WHITE)
    surface.blit(lines_label, (origin_x, origin_y - 16))

    field_rect = pygame.Rect(origin_x, origin_y, field_width, field_height)
    pygame.draw.rect(surface, DARK_GREY, field_rect)
    pygame.draw.rect(surface, OUTLINE_COLOR, field_rect, 2)

    # settled blocks
    for y in range(GRID_HEIGHT):
        for x in range(GRID_WIDTH):
            color = game.grid[y][x]
            if color is not None:
                bx = origin_x + x * cell
                by = origin_y + y * cell
                r = pygame.Rect(bx, by, cell, cell)
                pygame.draw.rect(surface, color, r)
                pygame.draw.rect(surface, OUTLINE_COLOR, r, 1)

    # ghost piece
    ghost_y = game.get_ghost_y()
    piece = game.current_piece
    shape = piece.shape
    for r in range(4):
        for c in range(4):
            if shape[r][c] == "#":
                gx = piece.x + c
                gy = ghost_y + r
                if gy < 0:
                    continue
                if 0 <= gy < GRID_HEIGHT and game.grid[gy][gx] is None:
                    bx = origin_x + gx * cell
                    by = origin_y + gy * cell
                    rct = pygame.Rect(bx, by, cell, cell)
                    pygame.draw.rect(surface, GHOST_COLOR, rct)
                    pygame.draw.rect(surface, OUTLINE_COLOR, rct, 1)

    # current falling piece
    for r in range(4):
        for c in range(4):
            if shape[r][c] == "#":
                gx = piece.x + c
                gy = piece.y + r
                if gy < 0:
                    continue
                bx = origin_x + gx * cell
                by = origin_y + gy * cell
                rct = pygame.Rect(bx, by, cell, cell)
                pygame.draw.rect(surface, piece.color, rct)
                pygame.draw.rect(surface, OUTLINE_COLOR, rct, 1)

    # grid lines
    for x in range(GRID_WIDTH + 1):
        sx = origin_x + x * cell
        pygame.draw.line(surface, GREY, (sx, origin_y),
                         (sx, origin_y + field_height))
    for y in range(GRID_HEIGHT + 1):
        sy = origin_y + y * cell
        pygame.draw.line(surface, GREY, (origin_x, sy),
                         (origin_x + field_width, sy))

    # impact flash overlay
    if game.impact_timer > 0.0 and game.impact_duration > 0.0:
        strength = game.impact_timer / game.impact_duration
        strength = max(0.0, min(1.0, strength))
        alpha = int(90 * strength)
        if alpha > 0:
            impact_surface = pygame.Surface(
                (field_width, field_height), pygame.SRCALPHA
            )
            impact_surface.fill((0, 255, 120, alpha))
            surface.blit(impact_surface, (origin_x, origin_y),
                         special_flags=pygame.BLEND_ADD)

    # local flash effect
    if game.is_clear_flash_active():
        flash = pygame.Surface((field_width, field_height), pygame.SRCALPHA)
        flash.fill((0, 255, 120, 100))
        surface.blit(flash, (origin_x, origin_y),
                     special_flags=pygame.BLEND_ADD)

def draw_crt_overlay(surface):
    w, h = surface.get_size()
    overlay = pygame.Surface((w, h), pygame.SRCALPHA)

    for y in range(0, h, 4):
        pygame.draw.line(overlay, (0, 30, 0, 70), (0, y), (w, y))

    pygame.draw.rect(overlay, (0, 0, 0, 140), (0, 0, w, h), 24)

    t = pygame.time.get_ticks() / 1000.0
    flicker_alpha = int(10 + 8 * math.sin(t * 7.0))
    if flicker_alpha > 0:
        flicker = pygame.Surface((w, h), pygame.SRCALPHA)
        flicker.fill((0, 0, 0, flicker_alpha))
        overlay.blit(flicker, (0, 0))

    surface.blit(overlay, (0, 0))


def apply_curved_crt(frame_surface, screen):
    src_w, src_h = frame_surface.get_size()
    sw, sh = screen.get_size()

    margin_x = 60
    margin_y = 50
    inner_w = src_w - 2 * margin_x
    inner_h = src_h - 2 * margin_y
    if inner_w <= 0 or inner_h <= 0:
        inner_w, inner_h = src_w, src_h

    curved = pygame.Surface((inner_w, inner_h), pygame.SRCALPHA)

    row_height = 2
    for y in range(0, inner_h, row_height):
        src_y = int(y * src_h / inner_h)
        h_slice = min(row_height, src_h - src_y)
        if h_slice <= 0:
            continue

        src_row = frame_surface.subsurface(
            pygame.Rect(0, src_y, src_w, h_slice)
        )

        ny = ((y + h_slice / 2) / inner_h) - 0.5
        scale = 1.0 - 0.08 * (abs(ny * 2.0) ** 2.5)
        dest_width = max(1, int(inner_w * scale))

        dest_row = pygame.transform.smoothscale(
            src_row, (dest_width, h_slice)
        )
        x_offset = (inner_w - dest_width) // 2
        curved.blit(dest_row, (x_offset, y))

    draw_crt_overlay(curved)

    mask = pygame.Surface((inner_w, inner_h), pygame.SRCALPHA)
    mask.fill((0, 0, 0, 0))
    pygame.draw.rect(
        mask,
        (255, 255, 255, 255),
        mask.get_rect(),
        border_radius=60,
    )
    curved.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)

    if sw == WINDOW_WIDTH and sh == WINDOW_HEIGHT:
        final = curved
    else:
        cw, ch = curved.get_size()
        scale = min(sw / cw, sh / ch)
        scaled_w = int(cw * scale)
        scaled_h = int(ch * scale)
        final = pygame.transform.smoothscale(curved, (scaled_w, scaled_h))

    screen.fill(BLACK)
    rect = final.get_rect(center=(sw // 2, sh // 2))
    screen.blit(final, rect)

# -------------------- VS MATCH --------------------


class TetrisVsMatch:
    """Handles TetrisLite VS vs CPU, with attacks + character animation."""

    def __init__(self, controls, sounds, speed_settings, cpu_frames, font):
        # Use "endless" physics for both sides
        self.player = TetrisGame("endless", controls, sounds, speed_settings)
        self.cpu = TetrisGame("endless", controls, sounds, speed_settings)

        self.sounds = sounds
        self.font = font
        self.cpu_frames = cpu_frames or []

        # attack / garbage
        self.player_attack_buffer = 0
        self.cpu_attack_buffer = 0

        # CPU AI state
        self.cpu_speed_scale = 0.9
        self.cpu_move_interval = 0.08
        self.cpu_move_timer = 0.0
        self.cpu_current_id = id(self.cpu.current_piece)
        self.cpu_target_x = self.cpu.current_piece.x
        self.cpu_target_rot = self.cpu.current_piece.rotation

        # CPU character anim
        self.cpu_frame_state = "idle"  # "idle", "send", "recv"
        self.cpu_frame_state_time = 0.0
        self.cpu_frame_state_duration = 0.6

    # ---------- CPU HELPERS ----------
    # ---------- CPU HEURISTIC EVAL ----------

    def _evaluate_grid(self, grid, lines_cleared):
        """Score a board: higher = better for the CPU."""
        heights = [0] * GRID_WIDTH
        holes = 0

        for x in range(GRID_WIDTH):
            seen_block = False
            for y in range(GRID_HEIGHT):
                if grid[y][x] is not None:
                    if not seen_block:
                        heights[x] = GRID_HEIGHT - y
                        seen_block = True
                elif seen_block:
                    # empty cell below a block = hole
                    holes += 1

        agg_height = sum(heights)
        bumpiness = 0
        for x in range(GRID_WIDTH - 1):
            bumpiness += abs(heights[x] - heights[x + 1])

        # Classic Tetris-bot style weights (roughly)
        score = (
            lines_cleared * 6.0
            - agg_height * 0.5
            - holes * 4.0
            - bumpiness * 0.3
        )
        return score

    def _find_best_move_for_current_piece(self):
        """Try every rotation/column; pick the best-scoring landing."""
        g = self.cpu
        name = g.current_piece.name

        best_score = None
        best_x = g.current_piece.x
        best_rot = g.current_piece.rotation

        for rot in range(4):
            shape = ROTATIONS[name][rot]

            # try columns, allow small negative start for kicks / I piece
            for x in range(-3, GRID_WIDTH):
                # if spawn collides, skip this column
                if g.check_collision(shape, x, -2):
                    continue

                # drop from y = -2 until we would collide
                y = -2
                while not g.check_collision(shape, x, y + 1):
                    y += 1

                # clone grid
                grid_copy = [row[:] for row in g.grid]
                top_out = False

                # place the piece onto the clone
                for r in range(4):
                    for c in range(4):
                        if shape[r][c] == "#":
                            gx = x + c
                            gy = y + r
                            # if any tile would lock above visible board, treat as bad
                            if gy < 0:
                                top_out = True
                                break
                            if not (0 <= gx < GRID_WIDTH and 0 <= gy < GRID_HEIGHT):
                                top_out = True
                                break
                            if grid_copy[gy][gx] is not None:
                                top_out = True
                                break
                            grid_copy[gy][gx] = PIECE_COLOR
                    if top_out:
                        break
                if top_out:
                    continue

                # simulate line clears
                cleared = 0
                new_grid = []
                for row in grid_copy:
                    if all(cell is not None for cell in row):
                        cleared += 1
                    else:
                        new_grid.append(row)
                while len(new_grid) < GRID_HEIGHT:
                    new_grid.insert(0, [None for _ in range(GRID_WIDTH)])

                score = self._evaluate_grid(new_grid, cleared)
                # tiny randomness so CPU isn't a robot
                score += random.uniform(-0.25, 0.25)

                if (best_score is None) or (score > best_score):
                    best_score = score
                    best_x = x
                    best_rot = rot

        return best_x, best_rot

    # ---------- CPU AI EVAL HELPERS ----------

    def _evaluate_grid_features(self, grid):
        heights = [0] * GRID_WIDTH
        holes = 0

        for x in range(GRID_WIDTH):
            seen_block = False
            for y in range(GRID_HEIGHT):
                if grid[y][x] is not None:
                    if not seen_block:
                        heights[x] = GRID_HEIGHT - y
                        seen_block = True
                else:
                    if seen_block:
                        holes += 1

        aggregate_height = sum(heights)
        max_height = max(heights)
        bumpiness = sum(abs(heights[i] - heights[i + 1])
                        for i in range(GRID_WIDTH - 1))
        return aggregate_height, max_height, holes, bumpiness

    def _simulate_cpu_drop(self, piece_name, rotation, x_pos):
        """Simulate dropping a piece at (x_pos, rotation) and score board."""
        shape = ROTATIONS[piece_name][rotation]

        # figure out occupied columns for valid X range
        min_c, max_c = 4, -1
        for r in range(4):
            for c in range(4):
                if shape[r][c] == "#":
                    min_c = min(min_c, c)
                    max_c = max(max_c, c)

        if max_c == -1:
            return None

        min_x = -min_c
        max_x = GRID_WIDTH - 1 - max_c
        if x_pos < min_x or x_pos > max_x:
            return None

        # drop from spawn y = -2
        y = -2
        while not self.cpu.check_collision(shape, x_pos, y + 1):
            y += 1

        if y < -2:
            return None  # immediately colliding, bad placement

        # copy grid and lock piece
        temp_grid = [row[:] for row in self.cpu.grid]
        for r in range(4):
            for c in range(4):
                if shape[r][c] == "#":
                    gx = x_pos + c
                    gy = y + r
                    if gy < 0:
                        return None
                    if 0 <= gy < GRID_HEIGHT:
                        temp_grid[gy][gx] = PIECE_COLOR

        # clear lines in temp board
        cleared = 0
        new_grid = []
        for row in temp_grid:
            if all(cell is not None for cell in row):
                cleared += 1
            else:
                new_grid.append(row)
        while len(new_grid) < GRID_HEIGHT:
            new_grid.insert(0, [None for _ in range(GRID_WIDTH)])

        agg_h, max_h, holes, bump = self._evaluate_grid_features(new_grid)

        # heuristic weights – tuned to prefer safe / clearing moves
        score = (
            agg_h * 0.4 +
            max_h * 0.2 +
            holes * 4.0 +
            bump * 0.3 -
            cleared * 5.0  # strongly reward line clears
        )

        return score, cleared, x_pos, rotation

    # ---------- CPU AI: play like a simple human that tries to clear lines ----------

    def _score_board(self, grid, lines_cleared):
        """Heuristic evaluation of a board position.

        Higher score is better.
        Prefers: low stack, few holes, low bumpiness, line clears.
        """
        heights = [0] * GRID_WIDTH
        holes = 0

        for x in range(GRID_WIDTH):
            block_seen = False
            column_holes = 0
            for y in range(GRID_HEIGHT):
                if grid[y][x] is not None:
                    if not block_seen:
                        block_seen = True
                        heights[x] = GRID_HEIGHT - y
                else:
                    if block_seen:
                        column_holes += 1
            holes += column_holes

        aggregate_height = sum(heights)
        max_height = max(heights)
        bumpiness = sum(abs(heights[i] - heights[i + 1])
                        for i in range(GRID_WIDTH - 1))

        # weights tuned to "feel" like someone trying to play decently
        return (
                lines_cleared * 5.0
                - aggregate_height * 0.5
                - bumpiness * 0.7
                - holes * 8.0
                - max_height * 0.3
        )

    def _simulate_cpu_drop(self, piece_name, rotation, x_start):
        """Simulate dropping a piece at (rotation, x_start) on a copy of the CPU grid.

        Returns (new_grid, lines_cleared, final_y) or None if the placement is invalid.
        """
        g = self.cpu
        shape = ROTATIONS[piece_name][rotation]

        # if spawn position already collides with walls, skip
        if g.check_collision(shape, x_start, -2):
            return None

        # drop until collision
        y = -2
        while not g.check_collision(shape, x_start, y + 1):
            y += 1

        # copy grid
        temp = [row[:] for row in g.grid]
        color = SHAPE_COLORS[piece_name]

        # paint piece into temp grid
        for r in range(4):
            for c in range(4):
                if shape[r][c] == "#":
                    gx = x_start + c
                    gy = y + r
                    if gy < 0 or gy >= GRID_HEIGHT or gx < 0 or gx >= GRID_WIDTH:
                        return None
                    temp[gy][gx] = color

        # clear full lines
        new_grid = []
        cleared = 0
        for row in temp:
            if all(cell is not None for cell in row):
                cleared += 1
            else:
                new_grid.append(row)
        while len(new_grid) < GRID_HEIGHT:
            new_grid.insert(0, [None for _ in range(GRID_WIDTH)])

        return new_grid, cleared, y

    def _plan_new_cpu_piece(self):
        """Choose a target rotation + x for the new CPU piece using the heuristic."""
        g = self.cpu
        piece = g.current_piece

        best_score = None
        best_rot = piece.rotation
        best_x = piece.x

        for rot in range(4):
            for x in range(-2, GRID_WIDTH - 1):
                result = self._simulate_cpu_drop(piece.name, rot, x)
                if result is None:
                    continue
                grid_after, cleared, _ = result
                score = self._score_board(grid_after, cleared)

                if best_score is None or score > best_score:
                    best_score = score
                    best_rot = rot
                    best_x = x

        self.cpu_target_rot = best_rot
        self.cpu_target_x = best_x
        self.cpu_current_id = id(piece)

    def _advance_cpu_effects(self, dt):
        """Advance timers (impact, line flash) for CPU board."""
        g = self.cpu
        if not g.game_over and not g.paused:
            g.elapsed_time += dt
            g.input_time += dt

        if g.impact_timer > 0.0:
            g.impact_timer = max(0.0, g.impact_timer - dt)

        if g.clear_flash_count > 0:
            g.clear_flash_elapsed += dt
            total_phases = g.clear_flash_count * 2
            if g.clear_flash_elapsed >= total_phases * g.clear_flash_interval:
                g.clear_flash_count = 0
                g.clear_flash_elapsed = 0.0

    def _drive_cpu_movement(self):
        """Rotate/move toward the planned target, then sometimes hard-drop."""
        g = self.cpu
        p = g.current_piece

        # clamp target into a reasonable range
        self.cpu_target_x = max(-2, min(self.cpu_target_x, GRID_WIDTH - 1))

        # 1) get rotation right
        if p.rotation != self.cpu_target_rot:
            g.rotate_piece()
            return

        # 2) move horizontally
        if p.x < self.cpu_target_x:
            g.move_piece(1)
            return
        if p.x > self.cpu_target_x:
            g.move_piece(-1)
            return

        # 3) in place: occasionally hard-drop, otherwise let gravity do work
        if random.random() < 0.15:
            g.hard_drop()

    def _update_cpu(self, dt):
        """One update step for the CPU side (gravity + AI moves)."""
        g = self.cpu
        if g.game_over:
            return

        # basic timers
        self._advance_cpu_effects(dt)

        # detect new piece and plan a move for it
        if id(g.current_piece) != self.cpu_current_id:
            self._plan_new_cpu_piece()

        # AI-controlled horizontal / rotation movement
        self.cpu_move_timer += dt
        if self.cpu_move_timer >= self.cpu_move_interval:
            self.cpu_move_timer = 0.0
            if not g.paused:
                self._drive_cpu_movement()

        # gravity + locking (same physics as player, but no soft-drop)
        fall_interval = g.get_fall_interval(False) * self.cpu_speed_scale
        g.fall_timer += dt
        if g.fall_timer >= fall_interval:
            g.fall_timer = 0.0
            new_y = g.current_piece.y + 1
            if g.check_collision(g.current_shape(), g.current_piece.x, new_y):
                if not g.on_ground:
                    g.on_ground = True
                    g.lock_timer = 0.0
                g.lock_timer += dt
                if g.lock_timer >= g.lock_delay:
                    g.on_ground = False
                    g.lock_timer = 0.0
                    g.lock_piece()
            else:
                g.current_piece.y = new_y
                g.on_ground = False
                g.lock_timer = 0.0

    # ---------- ATTACK / GARBAGE ----------

    def _handle_attacks(self):
        p_attack = self.player.attack_outgoing
        c_attack = self.cpu.attack_outgoing
        if p_attack == 0 and c_attack == 0:
            return

        # reset per-step attack counters
        self.player.attack_outgoing = 0
        self.cpu.attack_outgoing = 0




        # garbage canceling (like modern VS)
        to_cpu = max(0, p_attack - c_attack)
        to_player = max(0, c_attack - p_attack)

        if to_cpu > 0:
            self.cpu.apply_garbage(to_cpu)
            # cpu receives lines → frame 2
            if self.cpu_frames:
                self.cpu_frame_state = "recv"
                self.cpu_frame_state_time = pygame.time.get_ticks() / 1000.0

        if to_player > 0:
            self.player.apply_garbage(to_player)
            # cpu sends lines → frame 3
            if self.cpu_frames:
                self.cpu_frame_state = "send"
                self.cpu_frame_state_time = pygame.time.get_ticks() / 1000.0

    # ---------- CPU CHARACTER FRAME ----------

    def _get_cpu_frame(self):
        if not self.cpu_frames:
            return None
        t = pygame.time.get_ticks() / 1000.0
        if self.cpu_frame_state in ("send", "recv"):
            if t - self.cpu_frame_state_time < self.cpu_frame_state_duration:
                idx = 3 if self.cpu_frame_state == "send" else 2
                return self.cpu_frames[idx]
            else:
                self.cpu_frame_state = "idle"

        # idle: flip between 0 and 1 every second
        phase = int(t) % 2
        return self.cpu_frames[phase]

    # ---------- MAIN LOOP ----------

    def run(self, state, clock, font):
        running = True
        while running:
            dt = clock.tick(FPS) / 1000.0
            events = pygame.event.get()
            key_state = pygame.key.get_pressed()

            # global events (ESC, F11, quit)
            for ev in events:
                if ev.type == pygame.QUIT:
                    pygame.quit()
                    sys.exit()
                if ev.type == pygame.KEYDOWN:
                    if ev.key == pygame.K_ESCAPE:
                        running = False
                    if ev.key == pygame.K_F11:
                        toggle_fullscreen(state)

            # keep CPU paused in sync with player
            self.cpu.paused = self.player.paused

            # update player with real inputs
            self.player.update(dt, key_state, events)

            # update CPU AI
            if not self.player.game_over:
                self._update_cpu(dt)

            # attacks & garbage
            self._handle_attacks()

            # win/lose conditions
            if self.player.game_over or self.cpu.game_over:
                if self.player.game_over and not self.cpu.game_over:
                    self.player.win = False
                    self.player.message = "CPU wins."
                elif self.cpu.game_over and not self.player.game_over:
                    self.player.win = True
                    self.player.message = "You win!"
                else:
                    self.player.win = False
                    self.player.message = "Draw."

                game_over_loop(state, clock, font, self.player, "vs")
                break

            # --- RENDER VS SCREEN ---
            screen = state["screen"]
            frame = state["frame"]
            frame.fill(BLACK)

            # header
            header = "py-tetris :: TetrisLite VS"
            frame.blit(self.font.render(header, True, WHITE), (30, 20))

            frame = state["frame"]
            frame.fill(BLACK)

            # header
            header = "py-tetris :: TetrisLite VS"
            frame.blit(self.font.render(header, True, WHITE), (40, 20))

            field_width_vs = GRID_WIDTH * VS_BLOCK_SIZE
            field_height_vs = GRID_HEIGHT * VS_BLOCK_SIZE

            stats_w = 180
            gap = 32

            stats_x = 40
            origin_y = 100  # boards start here

            player_x = stats_x + stats_w + gap
            cpu_x = player_x + field_width_vs + gap
            cpu_box_x = cpu_x + field_width_vs + gap
            cpu_box_w = 170
            cpu_box_h = field_height_vs

            # ----- left STATS panel (matches your "Player stats/held piece..." box) -----
            stats_rect = pygame.Rect(stats_x, origin_y, stats_w, field_height_vs)
            pygame.draw.rect(frame, DARK_GREY, stats_rect)
            pygame.draw.rect(frame, OUTLINE_COLOR, stats_rect, 2)

            frame.blit(self.font.render("PLAYER STATS", True, WHITE),
                       (stats_x + 10, origin_y - 24))
            frame.blit(self.font.render(f"Lines: {self.player.lines_cleared}", True, WHITE),
                       (stats_x + 10, origin_y + 10))
            # (you can add held / next / abilities text in this same box later)

            # ----- middle: PLAYER and CPU boards, centered -----
            draw_vs_board(frame, self.player, self.font, "PLAYER", player_x, origin_y)
            draw_vs_board(frame, self.cpu, self.font, "CPU", cpu_x, origin_y)

            # ----- right: CPU CHARACTER box -----
            cpu_rect = pygame.Rect(cpu_box_x, origin_y, cpu_box_w, cpu_box_h)
            pygame.draw.rect(frame, DARK_GREY, cpu_rect)
            pygame.draw.rect(frame, OUTLINE_COLOR, cpu_rect, 2)

            frame.blit(self.font.render("CPU", True, WHITE),
                       (cpu_box_x + 10, origin_y - 24))
            frame.blit(self.font.render("CHARACTER", True, WHITE),
                       (cpu_box_x + 10, origin_y - 4))

            cpu_frame = self._get_cpu_frame()
            if cpu_frame is not None:
                w, h = cpu_frame.get_size()

                # fit into the TOP HALF of the CPU box, with a small margin
                max_w = cpu_box_w - 20
                max_h = cpu_box_h // 2 - 20
                scale = min(max_w / w, max_h / h, 4)

                new_w = max(1, int(w * scale))
                new_h = max(1, int(h * scale))

                if scale < 1.0:
                    scaled = pygame.transform.smoothscale(cpu_frame, (new_w, new_h))
                else:
                    scaled = pygame.transform.scale(cpu_frame, (new_w, new_h))

                dest_x = cpu_box_x + 10
                dest_y = origin_y + 10   # near the top of the panel
                frame.blit(scaled, (dest_x, dest_y))


            apply_curved_crt(frame, screen)
            pygame.display.flip()


            apply_curved_crt(frame, screen)
            pygame.display.flip()

# -------------------- FULLSCREEN STATE --------------------


def toggle_fullscreen(state):
    fullscreen = not state["fullscreen"]
    state["fullscreen"] = fullscreen

    if fullscreen:
        screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
    else:
        screen = pygame.display.set_mode((WINDOW_WIDTH, WINDOW_HEIGHT))

    state["screen"] = screen

# -------------------- MENUS --------------------


def key_name(code):
    try:
        return pygame.key.name(code)
    except Exception:
        return str(code)


def menu_loop(state, clock, small_font, controls, speed_settings, sounds):
    options = [
        "Sprint (100 lines)",
        "Endless",
        "TetrisLite (abilities)",
        "TetrisLite VS (CPU)",
        "Settings",
        "Quit",
    ]
    selected = 0

    while True:
        clock.tick(FPS)
        events = pygame.event.get()
        for ev in events:
            if ev.type == pygame.QUIT:
                pygame.quit()
                sys.exit()
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    pygame.quit()
                    sys.exit()
                if ev.key == pygame.K_F11:
                    toggle_fullscreen(state)
                elif ev.key == pygame.K_UP:
                    selected = (selected - 1) % len(options)
                    snd = sounds.get("menu_move")
                    if snd:
                        snd.play()
                elif ev.key == pygame.K_DOWN:
                    selected = (selected + 1) % len(options)
                    snd = sounds.get("menu_move")
                    if snd:
                        snd.play()
                elif ev.key in (pygame.K_RETURN, pygame.K_SPACE):
                    snd = sounds.get("menu_select")
                    if snd:
                        snd.play()
                    choice = options[selected]
                    if choice.startswith("Sprint"):
                        return "sprint"
                    elif choice.startswith("Endless"):
                        return "endless"
                    elif choice.startswith("TetrisLite (abilities)"):
                        return "lite"
                    elif choice.startswith("TetrisLite VS"):
                        return "vs"
                    elif choice.startswith("Settings"):
                        settings_loop(state, clock, small_font,
                                      controls, speed_settings, sounds)
                    elif choice.startswith("Quit"):
                        pygame.quit()
                        sys.exit()

        screen = state["screen"]
        frame = state["frame"]
        frame.fill(BLACK)

        x0, y0 = 30, 60
        lh = 26

        header = [
            "py-tetris [crt edition]",
            "",
            "select mode:",
            ""
        ]
        for i, line in enumerate(header):
            surf = small_font.render(line, True, WHITE)
            frame.blit(surf, (x0, y0 + i * lh))

        blink_on = (pygame.time.get_ticks() // 400) % 2 == 0
        start_y = y0 + len(header) * lh
        for i, opt in enumerate(options):
            arrow = "->" if (i == selected and blink_on) else "  "
            text = f"{arrow} {opt}"
            col = WHITE if i == selected else GREY
            surf = small_font.render(text, True, col)
            frame.blit(surf, (x0, start_y + i * lh))

        footer_y = start_y + len(options) * lh + 2 * lh
        hint = "[UP/DOWN] select  [ENTER] confirm  [F11] fullscreen  [ESC] quit"
        frame.blit(small_font.render(hint, True, GREY),
                   (x0, footer_y))

        apply_curved_crt(frame, screen)
        pygame.display.flip()


def settings_loop(state, clock, small_font,
                  controls, speed_settings, sounds):
    actions_order = [
        "move_left",
        "move_right",
        "soft_drop",
        "hard_drop",
        "rotate",
        "hold",
        "pause",
    ]
    action_labels = {
        "move_left": "Move Left",
        "move_right": "Move Right",
        "soft_drop": "Soft Drop",
        "hard_drop": "Hard Drop",
        "rotate": "Rotate",
        "hold": "Hold",
        "pause": "Pause",
    }

    selected = 0
    rebinding_action = None
    extra_start_idx = len(actions_order)
    total_items = len(actions_order) + 2  # DAS + ARR

    while True:
        clock.tick(FPS)
        events = pygame.event.get()
        for ev in events:
            if ev.type == pygame.QUIT:
                pygame.quit()
                sys.exit()
            if ev.type == pygame.KEYDOWN:
                if rebinding_action is not None:
                    if ev.key == pygame.K_ESCAPE:
                        rebinding_action = None
                    else:
                        controls[rebinding_action] = ev.key
                        rebinding_action = None
                        snd = sounds.get("menu_select")
                        if snd:
                            snd.play()
                else:
                    if ev.key == pygame.K_ESCAPE:
                        return
                    if ev.key == pygame.K_F11:
                        toggle_fullscreen(state)
                    elif ev.key == pygame.K_UP:
                        selected = (selected - 1) % total_items
                        snd = sounds.get("menu_move")
                        if snd:
                            snd.play()
                    elif ev.key == pygame.K_DOWN:
                        selected = (selected + 1) % total_items
                        snd = sounds.get("menu_move")
                        if snd:
                            snd.play()
                    elif ev.key in (pygame.K_RETURN, pygame.K_SPACE):
                        if selected < len(actions_order):
                            rebinding_action = actions_order[selected]
                            snd = sounds.get("menu_select")
                            if snd:
                                snd.play()
                    elif ev.key == pygame.K_LEFT:
                        if selected == extra_start_idx:
                            speed_settings["das_ms"] = max(
                                0, speed_settings["das_ms"] - 10)
                        elif selected == extra_start_idx + 1:
                            speed_settings["arr_ms"] = max(
                                10, speed_settings["arr_ms"] - 10)
                    elif ev.key == pygame.K_RIGHT:
                        if selected == extra_start_idx:
                            speed_settings["das_ms"] = min(
                                400, speed_settings["das_ms"] + 10)
                        elif selected == extra_start_idx + 1:
                            speed_settings["arr_ms"] = min(
                                300, speed_settings["arr_ms"] + 10)

        screen = state["screen"]
        frame = state["frame"]
        frame.fill(BLACK)

        x0, y0 = 30, 40
        lh = 24

        header = [
            "py-tetris :: settings",
            "",
            "key bindings (ENTER to rebind, ESC to exit):",
            ""
        ]
        for i, line in enumerate(header):
            surf = small_font.render(line, True, WHITE)
            frame.blit(surf, (x0, y0 + i * lh))

        blink_on = (pygame.time.get_ticks() // 400) % 2 == 0
        start_y = y0 + len(header) * lh

        for i, act in enumerate(actions_order):
            label = action_labels[act]
            key_str = key_name(controls[act])
            prefix = "->" if (i == selected and blink_on) else "  "
            text = f"{prefix} {label:<10} : {key_str}"
            col = WHITE if i == selected else GREY
            surf = small_font.render(text, True, col)
            frame.blit(surf, (x0, start_y + i * lh))

        das_idx = extra_start_idx
        arr_idx = extra_start_idx + 1
        y_das = start_y + len(actions_order) * lh + lh
        y_arr = y_das + lh

        prefix_d = "->" if (selected == das_idx and blink_on) else "  "
        prefix_a = "->" if (selected == arr_idx and blink_on) else "  "

        das_text = f"{prefix_d} DAS (ms): {speed_settings['das_ms']}"
        arr_text = f"{prefix_a} ARR (ms): {speed_settings['arr_ms']}"

        col_d = WHITE if selected == das_idx else GREY
        col_a = WHITE if selected == arr_idx else GREY
        frame.blit(small_font.render(das_text, True, col_d),
                   (x0, y_das))
        frame.blit(small_font.render(arr_text, True, col_a),
                   (x0, y_arr))

        hint = "[F11] fullscreen   [ESC] back"
        frame.blit(small_font.render(hint, True, GREY),
                   (x0, y_arr + 2 * lh))

        apply_curved_crt(frame, screen)
        pygame.display.flip()


def ability_choice_loop(state, clock, font, game):
    owned_ids = {ab["id"] for ab in game.abilities}
    pool = [a for a in ABILITY_DEFS if a["id"] not in owned_ids]
    if not pool:
        game.pending_ability_choice = False
        game.paused = False
        return

    random.shuffle(pool)
    choices = pool[:3]

    selected = 0
    stage = "pick"
    chosen_ability = None

    while True:
        clock.tick(FPS)
        events = pygame.event.get()
        for ev in events:
            if ev.type == pygame.QUIT:
                pygame.quit()
                sys.exit()
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_F11:
                    toggle_fullscreen(state)
                elif stage == "pick":
                    if ev.key == pygame.K_UP:
                        selected = (selected - 1) % len(choices)
                    elif ev.key == pygame.K_DOWN:
                        selected = (selected + 1) % len(choices)
                    elif ev.key in (pygame.K_RETURN, pygame.K_SPACE):
                        chosen_ability = choices[selected]
                        stage = "bind"
                elif stage == "bind":
                    if ev.key == pygame.K_ESCAPE:
                        stage = "pick"
                    else:
                        game.add_ability(chosen_ability, ev.key)
                        game.pending_ability_choice = False
                        game.paused = False
                        return

        screen = state["screen"]
        frame = state["frame"]
        frame.fill(BLACK)

        x0, y0 = 30, 60
        lh = 26

        if stage == "pick":
            header = [
                "upgrade console :: ability selection",
                "",
                f"lines cleared: {game.lines_cleared}",
                "",
                "choose one ability:",
                ""
            ]
        else:
            header = [
                "upgrade console :: key binding",
                "",
                f"selected: {chosen_ability['name']}",
                "",
                "press a key to bind this ability",
                "(ESC to cancel)",
                ""
            ]

        for i, line in enumerate(header):
            surf = font.render(line, True, WHITE)
            frame.blit(surf, (x0, y0 + i * lh))

        if stage == "pick":
            start_y = y0 + len(header) * lh
            blink_on = (pygame.time.get_ticks() // 400) % 2 == 0
            for i, ab in enumerate(choices):
                arrow = "->" if (i == selected and blink_on) else "  "
                title = f"{arrow} {ab['name']}"
                desc = f"   {ab['desc']}"
                frame.blit(font.render(title, True,
                                       WHITE if i == selected else GREY),
                           (x0, start_y + i * 2 * lh))
                frame.blit(font.render(desc, True, GREY),
                           (x0, start_y + i * 2 * lh + lh))
        else:
            start_y = y0 + len(header) * lh
            frame.blit(font.render(chosen_ability["desc"], True, GREY),
                       (x0, start_y))

        apply_curved_crt(frame, screen)
        pygame.display.flip()


def game_over_loop(state, clock, small_font, game, mode):
    while True:
        clock.tick(FPS)
        events = pygame.event.get()
        exit_now = False
        for ev in events:
            if ev.type == pygame.QUIT:
                pygame.quit()
                sys.exit()
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_F11:
                    toggle_fullscreen(state)
                else:
                    exit_now = True

        screen = state["screen"]
        frame = state["frame"]
        frame.fill(BLACK)

        x0, y0 = 30, 80
        lh = 28

        status = "RUN COMPLETE :: "
        status += "SUCCESS" if game.win else "FAILURE"

        lines = [
            "py-tetris :: run report",
            "",
            status,
            f"lines cleared : {game.lines_cleared}",
        ]

        if mode == "sprint":
            lines.append(f"time elapsed : {game.elapsed_time:0.2f}s")
        if mode == "vs":
            lines.append("mode         : VS (CPU)")

        if game.message:
            lines.append(f"status msg   : {game.message}")

        lines.append("")
        blink_on = (pygame.time.get_ticks() // 400) % 2 == 0
        prompt = ("-> press any key to return"
                  if blink_on else "   press any key to return")
        lines.append(prompt)

        for i, line in enumerate(lines):
            col = GREEN if ("SUCCESS" in line or "You win" in line) else \
                  RED if ("FAILURE" in line or "CPU wins" in line) else WHITE
            surf = small_font.render(line, True, col)
            frame.blit(surf, (x0, y0 + i * lh))

        apply_curved_crt(frame, screen)
        pygame.display.flip()

        if exit_now:
            return

# -------------------- MAIN --------------------


def main():
    pygame.init()
    try:
        pygame.mixer.init(frequency=44100, size=-16, channels=1)

        move_sound = create_tone(900, 40, 0.25)
        drop_sound = create_tone(300, 120, 0.35)

        menu_move = create_tone(1500, 30, 0.25)
        menu_select = create_tone(700, 80, 0.35)

        tetris_jingle = create_melody([900, 1200, 1500, 1800],
                                      note_ms=80, gap_ms=10,
                                      volume=0.35)
        game_over_jingle = create_melody([400, 300, 220],
                                         note_ms=150, gap_ms=15,
                                         volume=0.35)
        victory_jingle = create_melody([600, 900, 1200, 1500],
                                       note_ms=120, gap_ms=10,
                                       volume=0.35)

        clear_sounds = []
        for i in range(6):
            freq = 500 + i * 90
            clear_sounds.append(create_tone(freq, 140, 0.35))

        sounds = {
            "move": move_sound,
            "drop": drop_sound,
            "menu_move": menu_move,
            "menu_select": menu_select,
            "tetris": tetris_jingle,
            "game_over": game_over_jingle,
            "victory": victory_jingle,
        }
        for idx, s in enumerate(clear_sounds):
            sounds[f"clear_{idx}"] = s
        sounds["_clear_count"] = len(clear_sounds)
    except Exception:
        sounds = {}

    screen = pygame.display.set_mode((WINDOW_WIDTH, WINDOW_HEIGHT))
    pygame.display.set_caption("py-tetris [crt analog abilities + VS]")

    frame_surface = pygame.Surface((WINDOW_WIDTH, WINDOW_HEIGHT))

    state = {
        "screen": screen,
        "frame": frame_surface,
        "fullscreen": False,
    }

    clock = pygame.time.Clock()
    small_font = pygame.font.SysFont("consolas", 20)

    controls = DEFAULT_CONTROLS.copy()
    speed_settings = {"das_ms": 160, "arr_ms": 40}

    # load CPU character frames
    # load CPU character frames
    try:
        cpu_frames = [
            pygame.image.load("pixil-frame-0.png").convert_alpha(),
            pygame.image.load("pixil-frame-1.png").convert_alpha(),
            pygame.image.load("pixil-frame-2.png").convert_alpha(),
            pygame.image.load("pixil-frame-3.png").convert_alpha(),
        ]
    except Exception as e:
        print("Could not load CPU frames:", e)
        cpu_frames = []


    while True:
        mode = menu_loop(state, clock, small_font,
                         controls, speed_settings, sounds)

        if mode == "vs":
            vs = TetrisVsMatch(controls, sounds, speed_settings,
                               cpu_frames, small_font)
            vs.run(state, clock, small_font)
            continue

        game = TetrisGame(mode, controls, sounds, speed_settings)

        running = True
        while running:
            dt = clock.tick(FPS) / 1000.0
            events = pygame.event.get()
            key_state = pygame.key.get_pressed()

            for ev in events:
                if ev.type == pygame.QUIT:
                    pygame.quit()
                    sys.exit()
                if ev.type == pygame.KEYDOWN:
                    if ev.key == pygame.K_ESCAPE:
                        running = False
                    if ev.key == pygame.K_F11:
                        toggle_fullscreen(state)

            game.update(dt, key_state, events)

            if mode == "lite" and game.pending_ability_choice \
                    and not game.game_over:
                ability_choice_loop(state, clock, small_font, game)

            if game.game_over:
                game_over_loop(state, clock, small_font, game, mode)
                running = False
                break

            screen = state["screen"]
            frame = state["frame"]
            frame.fill(BLACK)
            draw_grid(frame, game, small_font, mode)
            apply_curved_crt(frame, screen)
            pygame.display.flip()


if __name__ == "__main__":
    main()
