"""Accumulate overlapping Nano partial windows without treating them as a full draft."""


def overlap_start(previous: str, current: str) -> int | None:
    """Locate an unambiguous prefix of the new window in the previous window.

    Ignore punctuation/case, but require at least six letters or digits. An
    uncertain overlap retains the old preview until a reliable update or final
    sentence arrives; guessing a boundary can duplicate or discard words.
    """
    indexed = [(index, folded) for index, char in enumerate(previous)
               for folded in char.lower() if folded.isalnum()]
    positions = [index for index, _ in indexed]
    before = "".join(char for _, char in indexed)
    after = "".join(folded for char in current for folded in char.lower() if folded.isalnum())
    if len(after) < 6:
        return None
    # KMP finds the longest prefix in linear time even for repetitive transcripts.
    borders = [0] * len(after)
    for index in range(1, len(after)):
        matched = borders[index - 1]
        while matched and after[index] != after[matched]:
            matched = borders[matched - 1]
        if after[index] == after[matched]:
            matched += 1
        borders[index] = matched
    matched = best = occurrences = boundary = 0
    for index, char in enumerate(before):
        while matched and char != after[matched]:
            matched = borders[matched - 1]
        if char == after[matched]:
            matched += 1
        if matched > best:
            best, occurrences, boundary = matched, 1, index - matched + 1
        elif matched == best:
            occurrences += 1
        if matched == len(after):
            matched = borders[matched - 1]
    return positions[boundary] if best >= 6 and occurrences == 1 else None


class RealtimePreview:
    """Keep confirmed sentences authoritative and preserve the sliding prefix."""

    def __init__(self) -> None:
        self.confirmed: tuple[str, ...] = ()
        self.prefix = ""
        self.window = ""
        self.window_start: int | None = None

    def update(
        self,
        sentences: list[str],
        partial: str,
        start_ms: int | None,
        *,
        final: bool = False,
    ) -> str:
        # Nano punctuates even an unfinished hypothesis. That terminal period
        # is provisional and otherwise flickers or becomes frozen at a merge.
        # Confirmed sentences/final results keep all authoritative punctuation.
        partial = partial.rstrip().rstrip("。.")
        confirmed = tuple(sentences)
        if confirmed != self.confirmed or final:
            self.confirmed = confirmed
            self.prefix = self.window = ""
            self.window_start = None
        if not final and partial:
            if start_ms is None or self.window_start is None:
                self.prefix, self.window, self.window_start = "", partial, start_ms
            elif start_ms < self.window_start:
                # A wider re-decode is authoritative for the pending utterance.
                self.prefix, self.window, self.window_start = "", partial, start_ms
            elif start_ms == self.window_start:
                self.window = partial
            else:
                boundary = overlap_start(self.window, partial)
                if boundary is not None:
                    self.prefix += self.window[:boundary]
                    self.window, self.window_start = partial, start_ms
        return "".join(self.confirmed) + self.prefix + self.window
