"""Small, Qt-independent shuffle bag with reversible playback history."""

import random

MIN_HISTORY_WINDOW = 1000


class ShuffleHistory:
    """Visit every queue entry once before repeating; Previous retraces history.

    The playlist signature makes queue edits safe. Indices are never reused from
    a previous playlist, and selecting a track explicitly starts a new forward
    history without putting already-visited tracks back in the shuffle bag.
    Previous retains at least a full queue cycle. Old history is trimmed in
    batches so continuous repeat playback cannot grow memory indefinitely.
    """

    def __init__(self, rng=None):
        self._rng = rng if rng is not None else random.Random()
        self.signature = None
        self.history = []
        self.cursor = -1
        self.remaining = []
        self._history_window = MIN_HISTORY_WINDOW

    def reset(self, signature, current=-1):
        """Replace the index space; no old history can address the new queue."""
        self.signature = tuple(signature)
        self._history_window = max(MIN_HISTORY_WINDOW, len(self.signature))
        valid = 0 <= current < len(self.signature)
        self.history = [current] if valid else []
        self.cursor = 0 if valid else -1
        self.remaining = [i for i in range(len(self.signature)) if i != current]
        self._rng.shuffle(self.remaining)

    def sync(self, signature, current=-1):
        signature = tuple(signature)
        if signature != self.signature:
            self.reset(signature, current)
        elif 0 <= current < len(signature):
            self.select(current)

    def select(self, index):
        if self.signature is None or not 0 <= index < len(self.signature):
            return
        if not self._record(index):
            return
        try:
            self.remaining.remove(index)
        except ValueError:
            pass

    def _record(self, index):
        """Record a new choice, discarding the abandoned forward-history branch."""
        if self.cursor >= 0 and self.history[self.cursor] == index:
            return False
        del self.history[self.cursor + 1:]
        self.history.append(index)
        if len(self.history) > 2 * self._history_window:
            # Batch trimming keeps retained history bounded without shifting
            # the list on every Next in a long-running repeat session.
            del self.history[:self._history_window]
        self.cursor = len(self.history) - 1
        return True

    def next(self, repeat_all=False):
        if not self.signature:
            return None
        if self.cursor + 1 < len(self.history):
            self.cursor += 1
            return self.history[self.cursor]
        if not self.remaining:
            if not repeat_all:
                return None
            current = self.history[self.cursor] if self.cursor >= 0 else -1
            self.remaining = [i for i in range(len(self.signature)) if i != current]
            if not self.remaining:
                self.remaining = [current]
            self._rng.shuffle(self.remaining)
        index = self.remaining.pop()
        # The item has already left the bag; select() would search the entire
        # remaining list for an item that cannot be there, making a cycle O(n²).
        self._record(index)
        return index

    def previous(self):
        if self.cursor < 0:
            return None
        if self.cursor > 0:
            self.cursor -= 1
        return self.history[self.cursor]
