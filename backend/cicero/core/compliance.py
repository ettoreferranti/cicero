"""Did each debater argue the side it was assigned? (FR-34)

Read from the *prose* of each turn, not from the stance poll. The poll asks a
debater to self-report and is independently known to be unreliable; the finding
this module exists to surface — five of twelve con-assigned opening turns arguing
the pro case — was only recoverable by reading what was actually written. See
``docs/superpowers/decisions/2026-08-06-debaters-do-not-hold-assigned-sides.md``.

Everything here derives from ``chamber.turns``, which is append-only, and from
``Participant.stance``, which is frozen once a chamber leaves draft. Nothing reads
mutable roster state — ``muted`` in particular — so a change made mid-debate cannot
retroactively rewrite what this reports.
"""

from __future__ import annotations

from dataclasses import dataclass

from cicero.core.consensus import parse_stance
from cicero.core.prompt_builder import build_compliance_messages
from cicero.domain.enums import Stance
from cicero.domain.models import Chamber, Participant, Turn
from cicero.providers.base import GenerateOptions, Provider, ProviderError

#: Where a turn's judged side is recorded. Absent means **not measured** — never
#: "compliant". A judge call that failed, a reply that did not parse and a turn
#: that was never judged are indistinguishable here, and all three are silence.
ARGUED_KEY = "argued"

#: Which stance opposes which. ``NEUTRAL`` is deliberately absent: a neutral
#: debater was never asked to oppose anything, so its silence on the losing side
#: is not evidence about the debate.
_OPPOSITE: dict[Stance, Stance] = {Stance.PRO: Stance.CON, Stance.CON: Stance.PRO}


def opposite_of(stance: Stance) -> Stance | None:
    """The polar opposite of ``stance``, or ``None`` for ``NEUTRAL``."""
    return _OPPOSITE.get(stance)


def argued_stance(turn: Turn) -> Stance | None:
    """The side ``turn`` was judged to argue, or ``None`` if it was not judged.

    ``Turn.metadata`` is ``dict[str, object]`` and round-trips through JSON, so the
    value under the key is arbitrary. Anything that is not exactly a stance word
    reads as unmeasured rather than raising: a malformed record must not be able to
    crash a debate, and must not be guessed at either.
    """
    value = turn.metadata.get(ARGUED_KEY)
    if not isinstance(value, str):
        return None
    try:
        return Stance(value)
    except ValueError:
        return None


@dataclass(frozen=True)
class ComplianceRecord:
    """How one debater's judged turns compare with the side it was assigned."""

    assigned: Stance
    #: Turns by this debater that were judged at all.
    judged: int
    #: Judged turns that argued the assigned side.
    held: int
    #: Judged turns that argued its polar opposite. ``judged - held -
    #: argued_against`` is the turns that argued neither.
    argued_against: int


def debater_compliance(chamber: Chamber, participant: Participant) -> ComplianceRecord:
    """Count ``participant``'s judged turns against its assigned stance."""
    against = opposite_of(participant.stance)
    judged = held = argued_against = 0
    for turn in chamber.turns:
        if turn.participant_id != participant.id:
            continue
        argued = argued_stance(turn)
        if argued is None:
            continue
        judged += 1
        if argued is participant.stance:
            held += 1
        elif against is not None and argued is against:
            argued_against += 1
    return ComplianceRecord(
        assigned=participant.stance,
        judged=judged,
        held=held,
        argued_against=argued_against,
    )


#: Shown when the chamber's winning position was never argued against by anyone,
#: while at least one debater was assigned to argue against it — and that debater
#: was actually measured. Deliberately narrow: it reports what the transcript
#: does not contain, which is a weaker and more defensible claim than saying a
#: debater failed.
UNOPPOSED_CAVEAT = (
    "No debater was judged to argue against the winning position, though one was "
    "assigned to. Nothing in the judged turns contests it — read the transcript "
    "before treating this as a tested result."
)


def _judged_sides(chamber: Chamber) -> set[Stance]:
    """Every side any judged turn in the chamber was found to argue."""
    return {
        side for turn in chamber.turns if (side := argued_stance(turn)) is not None
    }


def compliance_caveat(chamber: Chamber) -> str:
    """Whether this chamber's outcome went uncontested (see ``UNOPPOSED_CAVEAT``).

    Fires only when all four hold:

    1. the outcome names a ``PRO`` or ``CON`` winner — ``NEUTRAL`` has no polar
       opposite and a disagreement has no winner at all;
    2. some debater was *assigned* that opposite;
    3. at least one turn **by such a debater** was judged;
    4. no judged turn in the chamber argued that opposite.

    Condition 3 is narrower than "anything was judged" on purpose. If the only
    con-assigned debater's turns all failed to judge while the pro turns
    succeeded, the loose version fires and reports an absence that was really a
    gap in measurement.
    """
    consensus = chamber.consensus
    if consensus is None or consensus.winning_stance is None:
        return ""
    opposite = opposite_of(consensus.winning_stance)
    if opposite is None:
        return ""
    opposing = [p for p in chamber.participants if p.stance is opposite]
    if not opposing:
        return ""
    if not any(debater_compliance(chamber, p).judged for p in opposing):
        return ""
    if opposite in _judged_sides(chamber):
        return ""
    return UNOPPOSED_CAVEAT


def noncompliance_lines(chamber: Chamber) -> tuple[str, ...]:
    """One line per debater that argued against its assigned side at least once.

    Only ``PRO``- and ``CON``-assigned debaters can appear: a neutral debater is
    told to follow the evidence, so it has no side to abandon.
    """
    lines: list[str] = []
    for participant in chamber.participants:
        against = opposite_of(participant.stance)
        if against is None:
            continue
        record = debater_compliance(chamber, participant)
        if record.argued_against == 0:
            continue
        lines.append(
            f"{participant.display_name} (assigned {record.assigned.value}) "
            f"argued {against.value} in {record.argued_against} of "
            f"{record.judged} judged turns"
        )
    return tuple(lines)


#: Matches ``_POLL_MAX_TOKENS``. Generous for a one-word reply, and the poll's
#: measured value for the same shape of task; diverging would buy nothing.
COMPLIANCE_MAX_TOKENS = 512


@dataclass(frozen=True)
class JudgeReading:
    """One judge call: the side it named, and what it actually said.

    ``judge`` answers ``Stance | None``, which is the right contract for the
    debate loop — an unreadable verdict and a failed call are equally "not
    measured", and neither may be guessed at. It is the wrong contract for
    *measuring the judge*: the F10 quote-first run produced ``None`` on 32 of 32
    turns and its record could not say whether the cause was a provider error, an
    unparseable reply, or a judge that abstained, because the harness never saw
    anything else. This keeps the three apart.

    ``error`` set means the call never returned. ``reply`` set with ``stance``
    ``None`` means the judge answered something unreadable — and the answer is
    still here to read.
    """

    #: The parsed side, or ``None`` when the reply could not be read.
    stance: Stance | None
    #: The judge's verbatim reply, or ``None`` if the call failed outright.
    reply: str | None = None
    #: The provider's message when the call failed, else ``None``.
    error: str | None = None


class ComplianceJudge:
    """Reads one turn and names the side it argues.

    Holds the moderator's provider — the chamber's designated impartial party —
    but calls it with its own options: a one-word answer wants no reasoning, no
    temperature and a small budget, exactly as ``poll_stances`` does.
    """

    def __init__(self, provider: Provider, model: str) -> None:
        self._provider = provider
        self._options = GenerateOptions(
            model=model,
            max_tokens=COMPLIANCE_MAX_TOKENS,
            temperature=0.0,
            allow_reasoning=False,
        )

    async def read(self, topic: str, content: str) -> JudgeReading:
        """Judge ``content``, keeping the reply and any error (see `JudgeReading`).

        Used by the measurement harness, which needs to tell the failure modes
        apart. The debate loop wants ``judge``.
        """
        messages = build_compliance_messages(topic, content)
        try:
            result = await self._provider.generate(messages, self._options)
        except ProviderError as exc:
            return JudgeReading(stance=None, error=str(exc))
        return JudgeReading(stance=parse_stance(result.content), reply=result.content)

    async def judge(self, topic: str, content: str) -> Stance | None:
        """The side ``content`` argues, or ``None`` if it could not be read.

        Both failure modes collapse to ``None`` on purpose: a provider error and
        an unparseable reply are equally "not measured", and the caller records
        the absence rather than guessing.
        """
        return (await self.read(topic, content)).stance
