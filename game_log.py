from baseprobabilities import (
    defensive_rebound,
    free_throw,
    ft_made,
    gameOver,
    halfOver,
    heave,
    heave_made,
    inbound,
    inbound_success,
    media_timeout,
    move,
    move_cutoff,
    move_foul,
    move_success,
    no_passing_lane,
    offensive_charge,
    offensive_rebound,
    ot_tipoff,
    overtimeOver,
    overtimeStart,
    out_of_bounds_turnover,
    pass_ball,
    pass_deflected,
    pass_foul,
    pass_intercepted,
    pass_success,
    quarterOver,
    shot_blocked,
    shot_corner_three,
    shot_foul_blocked,
    shot_foul_made,
    shot_foul_missed,
    shot_inside,
    shot_made,
    shot_midrange,
    shot_missed,
    shot_paint,
    shot_three,
    shot_clock_violation,
    steal,
    steal_success,
    substitution,
    team_timeout,
    tipoff,
    tipoffAwayWin,
    tipoffHomeWin,
    timeout,
    turnover,
    move_trapped,
)


_SHOT_EVENTS = {
    shot_three,
    shot_corner_three,
    shot_inside,
    shot_paint,
    shot_midrange,
    heave,
}
_SHOT_FOUL_OUTCOMES = {shot_foul_made, shot_foul_missed, shot_foul_blocked}
_MOVEMENT_PASS_OUTCOMES = {
    move_success,
    move_cutoff,
    pass_success,
    no_passing_lane,
}


def _player_name(game, player_id):
    if not player_id:
        return ""
    for roster in (game.t1RosterById, game.t2RosterById):
        if player_id in roster.index:
            player = roster.loc[player_id]
            return (
                f"{player['position']} {player['first_name']} "
                f"{player['last_name']}"
            )
    return f"Player {player_id}"


def _team_name(team_names, team_id):
    return team_names.get(team_id, "Unknown team")


def _period_name(game, period):
    if period > game.periodPerGame:
        return f"OT{period - game.periodPerGame}"
    if game.league == "CBB":
        return f"{period}H"
    return f"{period}Q"


def _play_prefix(game, play, team_names):
    seconds = max(0, int(play.TimeOnClock))
    clock = f"{seconds // 60:02d}:{seconds % 60:02d}"
    score = (
        f"{game.t1} {play.HomeTeamScore} - "
        f"{play.AwayTeamScore} {game.t2}"
    )
    return f"{_period_name(game, play.Quarter)} {clock} | {score} |"


def _is_frontcourt(team_id, x_axis, home_team_id, away_team_id):
    if team_id == home_team_id:
        return x_axis >= 1
    if team_id == away_team_id:
        return x_axis <= -1
    return True


def _rebound_suffix(game, play, team_names):
    if not play.ReboundingPlayerID:
        return ""
    rebound_type = (
        "offensive"
        if play.ReboundOutcomeID == offensive_rebound
        else "defensive"
        if play.ReboundOutcomeID == defensive_rebound
        else "loose-ball"
    )
    rebounder = _player_name(game, play.ReboundingPlayerID)
    rebound_team = _team_name(team_names, play.ReboundTeamID)
    return f" {rebounder} grabs the {rebound_type} rebound for {rebound_team}."


def _describe_play(game, play, team_names):
    team = _team_name(team_names, play.TeamID)
    player = _player_name(game, play.BallCarrierID)
    outcome = play.OutcomeID

    if play.EventID in (tipoff, ot_tipoff):
        winner = (
            team
            if outcome in (tipoffHomeWin, tipoffAwayWin)
            else "Unknown team"
        )
        return f"{winner} wins the tipoff; {player} takes possession."

    if play.EventID == inbound:
        if outcome == inbound_success:
            return f"{team} inbounds the ball; {player} takes possession."
        return f"{team} inbounds the ball."

    if play.EventID == move:
        if outcome == move_success:
            return f"{player} advances the ball for {team}."
        if outcome == move_cutoff:
            return f"{player} is cut off while moving the ball for {team}."

    if play.EventID == pass_ball and outcome in (
        pass_success,
        no_passing_lane,
    ):
        if outcome == pass_success:
            receiver = _player_name(game, play.PassedPlayerID)
            return f"{player} passes to {receiver} for {team}."
        return f"{player}'s pass finds no lane for {team}."

    if play.EventID in _SHOT_EVENTS:
        shot_type = (
            "three-pointer"
            if play.EventID in (shot_three, shot_corner_three)
            else "heave"
            if play.EventID == heave
            else "two-point shot"
        )
        made = outcome in (shot_made, shot_foul_made, heave_made)
        result = "makes" if made else "misses"
        detail = f"{player} {result} a {shot_type} for {team}."
        if outcome in _SHOT_FOUL_OUTCOMES:
            detail += f" Shooting foul by {_player_name(game, play.FoulingPlayerID)}."
        if outcome in (shot_blocked, shot_foul_blocked):
            detail += f" Blocked by {_player_name(game, play.BlockingPlayerID)}."
        return detail + _rebound_suffix(game, play, team_names)

    if play.EventID == free_throw:
        result = "makes" if outcome == ft_made else "misses"
        return (
            f"{player} {result} a free throw for {team}."
            + _rebound_suffix(game, play, team_names)
        )

    if play.EventID == substitution:
        entering = _player_name(game, play.SubstitutePlayerID)
        leaving = _player_name(game, play.SubstitutedPlayerID)
        if leaving:
            return f"{team} substitution: {entering} enters the game for {leaving}."
        return f"{team} substitution: {entering} enters the game."

    if play.EventID == timeout:
        if outcome == media_timeout:
            return f"{team + ' ' if play.TeamID else ''}MEDIA TIMEOUT."
        if outcome == team_timeout:
            return f"{team} TEAM TIMEOUT."
        return f"{team} TIMEOUT."

    if play.EventID == halfOver:
        return f"Halftime. {game.t1} {play.HomeTeamScore} - {play.AwayTeamScore} {game.t2}."

    if play.EventID == quarterOver:
        return (
            f"End of {_period_name(game, play.Quarter)}. "
            f"{game.t1} {play.HomeTeamScore} - "
            f"{play.AwayTeamScore} {game.t2}."
        )

    if play.EventID == overtimeStart:
        return "The game is tied; heading to overtime."

    if play.EventID == overtimeOver:
        return f"End of {_period_name(game, play.Quarter)}."

    if play.EventID == gameOver:
        return (
            f"End of game. {game.t1} {play.HomeTeamScore} - "
            f"{play.AwayTeamScore} {game.t2}."
        )

    if play.InjuryID:
        return f"{_player_name(game, play.InjuryID)} is injured."

    if play.EventID == steal and outcome == steal_success:
        return (
            f"{_player_name(game, play.StealingPlayerID)} steals the ball "
            f"from {player}."
        )

    if play.EventID == turnover:
        if outcome == shot_clock_violation:
            return f"{team} turns the ball over on a shot-clock violation."
        if outcome == out_of_bounds_turnover:
            return f"{team} turns the ball over out of bounds."

    if outcome in (move_foul, pass_foul, offensive_charge):
        fouler = _player_name(game, play.FoulingPlayerID)
        if outcome == offensive_charge:
            return f"Offensive foul by {fouler} for {team}."
        return f"{fouler} fouls {player} for {team}."

    if play.EventID == pass_ball and outcome in (
        pass_deflected,
        pass_intercepted,
    ):
        return f"{team} pass is {('deflected' if outcome == pass_deflected else 'intercepted')}."

    if play.EventID == move and outcome == move_trapped:
        return f"{player} is trapped with the ball for {team}."

    return None


def render_game_log(game):
    home_team_id = int(game.t1team_df["id"].iloc[0])
    away_team_id = int(game.t2team_df["id"].iloc[0])
    team_names = {home_team_id: game.t1, away_team_id: game.t2}
    lines = [f"Game: {game.t1} vs {game.t2}"]
    pending_action = None

    for play in game.pbp.plays:
        if (
            play.EventID in (move, pass_ball)
            and play.OutcomeID in _MOVEMENT_PASS_OUTCOMES
        ):
            if _is_frontcourt(
                play.TeamID, play.XAxis, home_team_id, away_team_id
            ):
                pending_action = play
            continue

        if pending_action is not None:
            description = _describe_play(game, pending_action, team_names)
            if description:
                lines.append(
                    f"{_play_prefix(game, pending_action, team_names)} "
                    f"{description}"
                )
            pending_action = None

        description = _describe_play(game, play, team_names)
        if description:
            lines.append(f"{_play_prefix(game, play, team_names)} {description}")

    if pending_action is not None:
        description = _describe_play(game, pending_action, team_names)
        if description:
            lines.append(
                f"{_play_prefix(game, pending_action, team_names)} {description}"
            )

    return lines
