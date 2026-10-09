from dataclasses import dataclass, field
from typing import List


@dataclass
class BasePlayByPlay:
    GameID: int = 0
    Quarter: int = 0
    TimeOnClock: int = 0
    ShotClock: int = 0
    SecondsConsumed: int = 0
    HomeTeamScore: int = 0
    AwayTeamScore: int = 0
    TeamID: int = 0
    BallCarrierID: int = 0
    AssistingPlayerID: int = 0
    PassedPlayerID: int = 0
    DefenderID: int = 0
    SubstitutePlayerID: int = 0
    SubstitutedPlayerID: int = 0
    BlockingPlayerID: int = 0
    StealingPlayerID: int = 0
    FoulingPlayerID: int = 0
    InjuryID: int = 0
    InjuryType: int = 0
    InjuryDuration: int = 0
    PenaltyID: int = 0
    HomeOffensiveSystem: str = ""
    HomeDefensiveSystem: str = ""
    AwayOffensiveSystem: str = ""
    AwayDefensiveSystem: str = ""
    EventID: int = 0
    OutcomeID: int = 0
    XAxis: int = 0
    YAxis: int = 0
    NextXAxis: int = 0
    NextYAxis: int = 0
    ReboundingPlayerID: int = 0
    ReboundTeamID: int = 0
    ReboundOutcomeID: int = 0


class PlayByPlayCollector:
    def __init__(self) -> None:
        self.plays: List[BasePlayByPlay] = []

    def append(self, play: BasePlayByPlay) -> None:
        # Rebounds are folded into the missed attempt they follow rather than
        # being stored as separate plays.
        from baseprobabilities import (
            free_throw, ft_made, heave, rebound, shot_blocked,
            shot_corner_three, shot_inside, shot_midrange, shot_missed,
            shot_paint, shot_three,
        )

        if play.EventID == rebound and self.plays:
            last = self.plays[-1]
            shot_events = (
                shot_three, shot_corner_three, shot_inside, shot_paint,
                shot_midrange, heave,
            )
            missed = (
                last.OutcomeID in (shot_missed, shot_blocked)
                if last.EventID in shot_events
                else last.EventID == free_throw and last.OutcomeID != ft_made
            )
            if missed and not last.ReboundingPlayerID:
                last.ReboundingPlayerID = play.BallCarrierID
                last.ReboundTeamID = play.TeamID
                last.ReboundOutcomeID = play.OutcomeID
                return
        self.plays.append(play)

    def to_list(self) -> List[dict]:
        return [vars(p) for p in self.plays]
