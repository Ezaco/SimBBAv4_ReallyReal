

import math
import random
import pandas as pd
from datetime import datetime

from baseprobabilities import *
from bbdef import *
from courtDef import *
from import_dto import PlayerStatsDTO, TeamStatsDTO
from play_by_play_collector import PlayByPlayCollector, BasePlayByPlay



# Maps simulation DataFrame column names to Go API JSON field names
_PLAYER_FIELD_MAP = {
    "id":                 "PlayerID",
    "ID":          "PlayerID",
    "team_abbr":          "Team",
    "first_name":         "FirstName",
    "last_name":          "LastName",
    "position":           "Position",
    "height":             "Height",
    "weight":             "Weight",
    "stamina":            "Stamina",
    "bbiq":               "BasketballIQ",
    "shooting3":          "ThreePointShooting",
    "shooting2":          "MidRangeShooting",
    "finishing":          "InsideShooting",
    "free_throw":         "FreeThrow",
    "agility":            "Agility",
    "ballwork":           "Ballwork",
    "rebounding":         "Rebounding",
    "block":              "Blocking",
    "interior_defense":   "InteriorDefense",
    "perimeter_defense":  "PerimeterDefense",
}

_LINEUP_FIELD_MAP = {
    "id":                   "ID",
    "position":             "Position",
    "first_string_id":      "FirstStringID",
    "fs_minutes":           "FSMinutes",
    "fs_inside_proportion": "FSInsideProportion",
    "fs_mid_proportion":    "FSMidProportion",
    "fs_three_proportion":  "FSThreeProportion",
    "fs_shot_volume":       "FSShotVolume",
    "second_string_id":     "SecondStringID",
    "ss_minutes":           "SSMinutes",
    "ss_inside_proportion": "SSInsideProportion",
    "ss_mid_proportion":    "SSMidProportion",
    "ss_three_proportion":  "SSThreeProportion",
    "ss_shot_volume":       "SSShotVolume",
    "third_string_id":      "ThirdStringID",
    "ts_minutes":           "TSMinutes",
    "ts_inside_proportion": "TSInsideProportion",
    "ts_mid_proportion":    "TSMidProportion",
    "ts_three_proportion":  "TSThreeProportion",
    "ts_shot_volume":       "TSShotVolume",
}

_GAMEPLAN_FIELD_MAP = {
    "pace":                "Pace",
    "offensive_formation": "OffensiveFormation",
    "defensive_formation": "DefensiveFormation",
    "focus_player":        "FocusPlayer",
    "preserve_timeouts":   "PreserveTimeouts",
    "trigger1_enabled":    "Trigger1Enabled",
    "trigger1_type":       "Trigger1Type",
    "trigger1_value":      "Trigger1Value",
    "trigger2_enabled":    "Trigger2Enabled",
    "trigger2_value":      "Trigger2Value",
    "trigger3_enabled":    "Trigger3Enabled",
    "trigger3_value":      "Trigger3Value",
    "trigger3_exhaustion": "Trigger3Exhaustion",
    "trigger4_enabled":    "Trigger4Enabled",
    "trigger4_value":      "Trigger4Value",
}


def roster_df_from_api(players):
    rows = []
    for p in players:
        row = {sim_col: p.get(api_field) for sim_col, api_field in _PLAYER_FIELD_MAP.items()}
        # PlayerID can come through as 0/missing; the row's own ID is the fallback
        if not row["ID"]:
            row["ID"] = row["id"] = p.get("ID")
        rows.append(row)
    return pd.DataFrame(rows)


def lineup_df_from_api(lineup):
    rows = [{sim_col: entry.get(api_field) for sim_col, api_field in _LINEUP_FIELD_MAP.items()} for entry in lineup]
    return pd.DataFrame(rows)


def gameplan_df_from_api(gameplan):
    row = {sim_col: gameplan.get(api_field) for sim_col, api_field in _GAMEPLAN_FIELD_MAP.items()}
    return pd.DataFrame([row])


def team_df_from_api(team):
    return pd.DataFrame([{"id": team.get("ID"), "abbr": team.get("Abbr")}])


def pair_substitutions(previousLineup, newLineup):
    # Returns (entering player ID, replaced player ID) for each player who came onto the court.
    # A player is paired with whoever held the same slot before; leftovers are paired in slot order.
    previousBySlot = {slot: int(player["ID"]) for slot, player in previousLineup.items()}
    newBySlot = {slot: int(player["ID"]) for slot, player in newLineup.items()}
    previousPlayerIds = set(previousBySlot.values())
    newPlayerIds = set(newBySlot.values())
    leavingIds = [pid for pid in previousBySlot.values() if pid not in newPlayerIds]
    entering = [(slot, pid) for slot, pid in newBySlot.items() if pid not in previousPlayerIds]
    replacedById = {}
    for slot, pid in entering:
        replacedId = previousBySlot.get(slot)
        if replacedId in leavingIds:
            replacedById[pid] = replacedId
            leavingIds.remove(replacedId)
    pairs = []
    for slot, pid in entering:
        if pid not in replacedById:
            replacedById[pid] = leavingIds.pop(0) if leavingIds else 0
        pairs.append((pid, replacedById[pid]))
    return pairs


class GameState:
    def __init__(self, match, gamenum=1):
        match_data = match["MatchData"]
        self.gid = match["ID"]
        self.t1 = match_data["HomeTeam"]["Abbr"]
        self.t2 = match_data["AwayTeam"]["Abbr"]
        self.league = str(match_data.get("League") or "").strip()
        if self.league not in ("CBB", "NBA"):
            raise ValueError(f"League must be 'CBB' or 'NBA', got: {repr(self.league)}")
        self.gamenum = gamenum
        self.HCA = float(match_data.get("HomeCourtAdvantage", 0.0))
        self.HCAAdj = round(self.HCA / 75, 12)
        self.gameType = self.league
        self.gameOn = True
        self.periodOn = True
        self.period = 1
        self.t1pts = 0; self.t2pts = 0
        self.t1q1pts = 0; self.t1q2pts = 0; self.t1q3pts = 0; self.t1q4pts = 0; self.t1qotpts = 0
        self.t2q1pts = 0; self.t2q2pts = 0; self.t2q3pts = 0; self.t2q4pts = 0; self.t2qotpts = 0
        self.t13a = 0; self.t13m = 0; self.t12a = 0; self.t12m = 0
        self.t23a = 0; self.t23m = 0; self.t22a = 0; self.t22m = 0
        if self.gameType == "CBB":
            self.qtrTime = 1200; self.otQtrTime = 300; self.periodPerGame = 2
            self.shotClock = 30; self.shotClockReset = 20; self.pInd = "H"; self.foulOutLimit = 5
        else:
            self.qtrTime = 700; self.otQtrTime = 300; self.periodPerGame = 4
            self.shotClock = 24; self.shotClockReset = 14; self.pInd = "Q"; self.foulOutLimit = 6
        self.cbbMediaTimeoutMarks = [960, 720, 480, 240]
        self.cbbMediaTimeoutsTaken = {p: set() for p in range(1, self.periodPerGame + 1)}
        self.nbaTimeoutEventsByPeriod = {p: 0 for p in range(1, self.periodPerGame + 1)}
        self.currTime = self.qtrTime
        self.currShotClock = self.shotClock
        self.courtPos = (0, 3)
        self.crossed_midcourt = False
        self.t1roster_df = roster_df_from_api(match_data["HomeTeamRoster"])
        self.t2roster_df = roster_df_from_api(match_data["AwayTeamRoster"])
        self.t1FouledOutPlayerIds = set(); self.t2FouledOutPlayerIds = set()
        self.t1FirstHalfTeamFouls = 0; self.t1SecondHalfTeamFouls = 0
        self.t2FirstHalfTeamFouls = 0; self.t2SecondHalfTeamFouls = 0
        self.t1stats = pd.DataFrame().assign(id=self.t1roster_df['ID'], Team=self.t1roster_df['team_abbr'], FName=self.t1roster_df['first_name'], LName=self.t1roster_df['last_name'], Position=self.t1roster_df['position'])
        self.t2stats = pd.DataFrame().assign(id=self.t2roster_df['ID'], Team=self.t2roster_df['team_abbr'], FName=self.t2roster_df['first_name'], LName=self.t2roster_df['last_name'], Position=self.t2roster_df['position'])
        for stats in (self.t1stats, self.t2stats):
            for col in ('MP','Assist','Ins Shot Att','Ins Shot Made','Ins Shot %','Mid Shot Att','Mid Shot Made','Mid Shot %','3PT Shot Att','3PT Shot Made','3PT Shot %','FT Shot Att','FT Shot Made','FT Shot %','OREB','DREB','Stl','Blk','TO','Foul','Pts'):
                stats[col] = 0.00 if col == 'MP' else 0
            stats.set_index('id', inplace=True)
        self.t1team_df = team_df_from_api(match_data["HomeTeam"])
        self.t2team_df = team_df_from_api(match_data["AwayTeam"])
        self.t1Lineup_df = lineup_df_from_api(match_data["HomeTeamLineup"])
        self.t2Lineup_df = lineup_df_from_api(match_data["AwayTeamLineup"])
        self.t1Gameplan_df = gameplan_df_from_api(match_data["HomeTeamGameplan"])
        self.t2Gameplan_df = gameplan_df_from_api(match_data["AwayTeamGameplan"])
        if self.t1Gameplan_df.empty or self.t2Gameplan_df.empty:
            raise ValueError("Both teams require a gameplan record.")
        self.paceStaminaModifiers = {"Very Slow":1.15,"Slow":1.10,"Balanced":1.00,"Fast":0.90,"Very Fast":0.85}
        self.paceActionTimeModifiers = {"Very Slow":1.45,"Slow":1.25,"Balanced":1.00,"Fast":0.75,"Very Fast":0.55}
        self.paceActionTimeWeights = {"Very Slow":[50,25,15,7,3],"Slow":[25,40,25,8,2],"Balanced":[10,20,40,20,10],"Fast":[2,8,25,40,25],"Very Fast":[3,7,15,25,50]}
        self.paceActionTimeCategories = list(self.paceActionTimeModifiers.keys())
        self.paceShotClockUrgencyStarts = {"CBB":{"Very Slow":6.0,"Slow":8.0,"Balanced":10.0,"Fast":14.0,"Very Fast":18.0},"NBA":{"Very Slow":5.0,"Slow":7.0,"Balanced":10.0,"Fast":13.0,"Very Fast":16.0}}
        self.offensiveFormationDestinationWeights = {"Balanced":{"inside":1.00,"midrange":1.00,"three":1.00},"Motion":{"inside":1.06,"midrange":0.97,"three":1.04},"Pick-and-Roll":{"inside":1.08,"midrange":1.04,"three":0.94},"Post-Up":{"inside":1.12,"midrange":0.96,"three":0.90},"Space-and-Post":{"inside":0.94,"midrange":1.05,"three":1.10}}
        self.defensiveFormationDestinationWeights = {"Man-to-Man":{"inside":1.00,"midrange":1.00,"three":1.00},"1-3-1 Zone":{"inside":1.05,"midrange":0.92,"three":1.05},"3-2 Zone":{"inside":1.08,"midrange":1.02,"three":0.90},"2-3 Zone":{"inside":0.90,"midrange":1.02,"three":1.08},"Box-and-One Zone":{"inside":1.00,"midrange":1.00,"three":1.00}}
        self.lineupPreferenceNeutralShare = 100 / 3
        self.lineupPreferenceDestinationStrength = 0.35
        self.lineupPreferenceDestinationMinimum = 0.80
        self.lineupPreferenceDestinationMaximum = 1.20
        self.playerShotWillingnessPerProportionPoint = 0.0015
        self.playerShotWillingnessMaximumAdjustment = 0.03
        self.passReceiverPreferenceWeightPerProportionPoint = 0.015
        self.passReceiverPreferenceMinimumWeight = 0.65
        self.passReceiverPreferenceMaximumWeight = 1.35
        self.offensiveFormationReboundAdjustments = {"Balanced":0.000,"Motion":-0.005,"Pick-and-Roll":-0.005,"Post-Up":0.010,"Space-and-Post":-0.005}
        self.defensiveFormationOffensiveReboundAdjustments = {"Man-to-Man":0.000,"1-3-1 Zone":0.005,"3-2 Zone":0.005,"2-3 Zone":-0.010,"Box-and-One Zone":0.005}
        self.doubleTeamAdjustments = {"Man-to-Man":{"focus":{"shot":-0.020,"movement":-0.030,"pass_deflection":0.015},"teammate":{"shot":0.005,"movement":0.0075,"pass_deflection":-0.00375}},"Box-and-One Zone":{"focus":{"shot":-0.030,"movement":-0.040,"pass_deflection":0.020},"teammate":{"shot":0.0075,"movement":0.010,"pass_deflection":-0.005}}}
        self.t1Pace = str(self.t1Gameplan_df["pace"].iloc[0]).strip()
        self.t2Pace = str(self.t2Gameplan_df["pace"].iloc[0]).strip()
        if self.t1Pace == '':
            self.t1Pace = 'Balanced'
        if self.t2Pace == '':
            self.t2Pace = 'Balanced'
        if self.t1Pace not in self.paceStaminaModifiers or self.t2Pace not in self.paceStaminaModifiers:
            raise ValueError("Pace must be Very Slow, Slow, Balanced, Fast, or Very Fast.")
        self.t1PaceStaminaModifier = self.paceStaminaModifiers[self.t1Pace]
        self.t2PaceStaminaModifier = self.paceStaminaModifiers[self.t2Pace]
        self.t1OffensiveFormation = str(self.t1Gameplan_df["offensive_formation"].iloc[0] or "Balanced").strip()
        self.t2OffensiveFormation = str(self.t2Gameplan_df["offensive_formation"].iloc[0] or "Balanced").strip()
        self.t1DefensiveFormation = str(self.t1Gameplan_df["defensive_formation"].iloc[0] or "Man-to-Man").strip()
        self.t2DefensiveFormation = str(self.t2Gameplan_df["defensive_formation"].iloc[0] or "Man-to-Man").strip()
        t1fpRaw = self.t1Gameplan_df["focus_player"].iloc[0]
        t2fpRaw = self.t2Gameplan_df["focus_player"].iloc[0]
        self.t1FocusPlayerId = int(t1fpRaw) if not pd.isna(t1fpRaw) and str(t1fpRaw).strip().isdigit() and int(t1fpRaw) > 0 else None
        self.t2FocusPlayerId = int(t2fpRaw) if not pd.isna(t2fpRaw) and str(t2fpRaw).strip().isdigit() and int(t2fpRaw) > 0 else None
        if self.t1OffensiveFormation not in self.offensiveFormationDestinationWeights or self.t2OffensiveFormation not in self.offensiveFormationDestinationWeights:
            raise ValueError("Offensive formation must be Balanced, Motion, Pick-and-Roll, Post-Up, or Space-and-Post.")
        if self.t1DefensiveFormation not in self.defensiveFormationDestinationWeights or self.t2DefensiveFormation not in self.defensiveFormationDestinationWeights:
            raise ValueError("Defensive formation must be Man-to-Man, 1-3-1 Zone, 3-2 Zone, 2-3 Zone, or Box-and-One Zone.")
        self.t1FormationDestinationWeights = {z: self.offensiveFormationDestinationWeights[self.t1OffensiveFormation][z] * self.defensiveFormationDestinationWeights[self.t2DefensiveFormation][z] for z in ("inside","midrange","three")}
        self.t2FormationDestinationWeights = {z: self.offensiveFormationDestinationWeights[self.t2OffensiveFormation][z] * self.defensiveFormationDestinationWeights[self.t1DefensiveFormation][z] for z in ("inside","midrange","three")}
        self.t1FormationOffensiveReboundAdjustment = self.offensiveFormationReboundAdjustments[self.t1OffensiveFormation] + self.defensiveFormationOffensiveReboundAdjustments[self.t2DefensiveFormation]
        self.t2FormationOffensiveReboundAdjustment = self.offensiveFormationReboundAdjustments[self.t2OffensiveFormation] + self.defensiveFormationOffensiveReboundAdjustments[self.t1DefensiveFormation]
        self.t1ExpectedActionTimeModifier = sum(self.paceActionTimeModifiers[c] * w for c, w in zip(self.paceActionTimeCategories, self.paceActionTimeWeights[self.t1Pace])) / sum(self.paceActionTimeWeights[self.t1Pace])
        self.t2ExpectedActionTimeModifier = sum(self.paceActionTimeModifiers[c] * w for c, w in zip(self.paceActionTimeCategories, self.paceActionTimeWeights[self.t2Pace])) / sum(self.paceActionTimeWeights[self.t2Pace])
        self.t1roster_df["base_stamina"] = self.t1roster_df["stamina"].astype(float)
        self.t2roster_df["base_stamina"] = self.t2roster_df["stamina"].astype(float)
        self.t1roster_df["stamina"] = self.t1roster_df["base_stamina"] * self.t1PaceStaminaModifier
        self.t2roster_df["stamina"] = self.t2roster_df["base_stamina"] * self.t2PaceStaminaModifier
        self.t1teamstats = pd.DataFrame().assign(id=self.t1team_df['id'], Team=self.t1team_df['abbr'])
        self.t2teamstats = pd.DataFrame().assign(id=self.t2team_df['id'], Team=self.t2team_df['abbr'])
        for ts in (self.t1teamstats, self.t2teamstats):
            for col in ('Assist','Ins Shot Att','Ins Shot Made','Ins Shot %','Mid Shot Att','Mid Shot Made','Mid Shot %','3PT Shot Att','3PT Shot Made','3PT Shot %','FT Shot Att','FT Shot Made','FT Shot %','OREB','DREB','Stl','Blk','TO','Foul','Poss','Pts'): ts[col] = 0
            ts['TOP'] = "00:00.0"; ts.set_index('id', inplace=True)
        self.t1teamscore = pd.DataFrame().assign(id=self.t1team_df['id'], Team=self.t1team_df['abbr'])
        self.t2teamscore = pd.DataFrame().assign(id=self.t2team_df['id'], Team=self.t2team_df['abbr'])
        for sc in (self.t1teamscore, self.t2teamscore):
            for col in ('P1','P2','P3','P4','OT'): sc[col] = 0
            sc.set_index('id', inplace=True)
        self.t1Probabilities = {"steal": stealProbability,"other_turnover": otherTurnoverProbability,"move": moveAttemptProbability,"pass": passAttemptProbability,"shot": shotAttemptProbability}
        self.t2Probabilities = dict(self.t1Probabilities)
        self.t1Players, self.t2Players = getPlayers(self.t1Lineup_df, self.t2Lineup_df)
        self.t1FoulProtection = {"enabled":int(self.t1Gameplan_df["trigger1_enabled"].iloc[0] or 0),"type":int(self.t1Gameplan_df["trigger1_type"].iloc[0] or 0),"value":int(self.t1Gameplan_df["trigger1_value"].iloc[0] or 0)}
        self.t2FoulProtection = {"enabled":int(self.t2Gameplan_df["trigger1_enabled"].iloc[0] or 0),"type":int(self.t2Gameplan_df["trigger1_type"].iloc[0] or 0),"value":int(self.t2Gameplan_df["trigger1_value"].iloc[0] or 0)}
        self.t1TimeoutGameplan = {"preserve":int(self.t1Gameplan_df["preserve_timeouts"].iloc[0] or 0),"trigger2_enabled":int(self.t1Gameplan_df["trigger2_enabled"].iloc[0] or 0),"trigger2_value":int(self.t1Gameplan_df["trigger2_value"].iloc[0] or 0),"trigger3_enabled":int(self.t1Gameplan_df["trigger3_enabled"].iloc[0] or 0),"trigger3_value":int(self.t1Gameplan_df["trigger3_value"].iloc[0] or 0),"trigger3_exhaustion":int(self.t1Gameplan_df["trigger3_exhaustion"].iloc[0] or 0),"trigger4_enabled":int(self.t1Gameplan_df["trigger4_enabled"].iloc[0] or 0),"trigger4_value":int(self.t1Gameplan_df["trigger4_value"].iloc[0] or 0)}
        self.t2TimeoutGameplan = {"preserve":int(self.t2Gameplan_df["preserve_timeouts"].iloc[0] or 0),"trigger2_enabled":int(self.t2Gameplan_df["trigger2_enabled"].iloc[0] or 0),"trigger2_value":int(self.t2Gameplan_df["trigger2_value"].iloc[0] or 0),"trigger3_enabled":int(self.t2Gameplan_df["trigger3_enabled"].iloc[0] or 0),"trigger3_value":int(self.t2Gameplan_df["trigger3_value"].iloc[0] or 0),"trigger3_exhaustion":int(self.t2Gameplan_df["trigger3_exhaustion"].iloc[0] or 0),"trigger4_enabled":int(self.t2Gameplan_df["trigger4_enabled"].iloc[0] or 0),"trigger4_value":int(self.t2Gameplan_df["trigger4_value"].iloc[0] or 0)}
        self.t1RosterById = indexRoster(self.t1roster_df)
        self.t2RosterById = indexRoster(self.t2roster_df)
        self.t1RecoveryMinutes = {int(pid): 0.0 for pid in self.t1RosterById.index}
        self.t2RecoveryMinutes = {int(pid): 0.0 for pid in self.t2RosterById.index}
        self.t1FoulProtectionBench = set(); self.t2FoulProtectionBench = set()
        self.t1PlayerHalfFouls = {}; self.t2PlayerHalfFouls = {}
        self.momentum = 0.0
        self.teamTimeoutsRemaining = {self.t1:(4 if self.league == "CBB" else 7), self.t2:(4 if self.league == "CBB" else 7)}
        self.teamTimeoutTriggerLatches = {self.t1:{"trigger2":False,"trigger3":False,"trigger4":False}, self.t2:{"trigger2":False,"trigger3":False,"trigger4":False}}
        self.teamTimeoutPreservationAnnounced = {self.t1: False, self.t2: False}
        self.teamTimeoutBlockedUntilLiveAction = False
        self.t1onCourt, self.t2onCourt, self.lineupParameters = self.pullSubs(True)
        self.t1TipChance = ((self.t1onCourt["c1"]["height"] - self.t2onCourt["c1"]["height"]) * 0.1) + 0.5
        self.t2TipChance = 1 - self.t1TipChance
        self.possTeam = "TIPOFF"
        self.possPlayer = ""
        self.assistPlayer = None
        self.assistMovementCount = 0
        self.defendedPlayerId = None
        self.currentDefender = None
        self.previousDefender = None
        self.finalHeavePending = False
        self.teamPossessions = {self.t1: 0, self.t2: 0}
        self.teamPossessionTime = {self.t1: 0.0, self.t2: 0.0}
        self.lastCountedPossessionTeam = None
        self.t1LargestLead = 0
        self.t2LargestLead = 0
        self.pbp = PlayByPlayCollector()

    # ------------------------------------------------------------------ helpers

    def getActiveLineupPreferences(self, onCourt):
        if not onCourt:
            return {"inside":self.lineupPreferenceNeutralShare,"midrange":self.lineupPreferenceNeutralShare,"three":self.lineupPreferenceNeutralShare}
        return {"inside":sum(float(p["inside_preference"]) for p in onCourt.values()) / len(onCourt),"midrange":sum(float(p["midrange_preference"]) for p in onCourt.values()) / len(onCourt),"three":sum(float(p["three_preference"]) for p in onCourt.values()) / len(onCourt)}

    def getActiveDestinationWeights(self, onCourt, formationDestinationWeights):
        prefs = self.getActiveLineupPreferences(onCourt)
        pw = {}
        for zone in ("inside","midrange","three"):
            w = (prefs[zone] / self.lineupPreferenceNeutralShare) ** self.lineupPreferenceDestinationStrength if prefs[zone] > 0 else self.lineupPreferenceDestinationMinimum
            pw[zone] = max(self.lineupPreferenceDestinationMinimum, min(self.lineupPreferenceDestinationMaximum, w))
        return {zone: formationDestinationWeights[zone] * pw[zone] for zone in ("inside","midrange","three")}, prefs

    def getPreferenceZone(self, shotZone):
        if shotZone in ("inside","paint"): return "inside"
        if shotZone == "midrange": return "midrange"
        if shotZone in ("three","corner_three"): return "three"
        return None

    def foulProtectionActive(self):
        if self.period > self.periodPerGame: return False
        if self.period == self.periodPerGame and self.currTime <= 120: return False
        return True

    def registerFoulProtection(self, teamNumber, playerStatId, playerId, playerLabel):
        protection = self.t1FoulProtection if teamNumber == 1 else self.t2FoulProtection
        halfFouls = self.t1PlayerHalfFouls if teamNumber == 1 else self.t2PlayerHalfFouls
        bench = self.t1FoulProtectionBench if teamNumber == 1 else self.t2FoulProtectionBench
        halfNumber = 1 if (self.period == 1 if self.league == "CBB" else self.period <= 2) else 2
        key = (halfNumber, int(playerId))
        halfFouls[key] = halfFouls.get(key, 0) + 1
        if not self.foulProtectionActive() or protection["enabled"] != 1: return False
        if protection["type"] == 1: threshold = 2; playerProtected = int(playerStatId) == protection["value"]
        elif protection["type"] == 2: threshold = protection["value"]; playerProtected = threshold >= 1
        else: return False
        if not playerProtected or halfFouls[key] < threshold or int(playerId) in bench: return False
        bench.add(int(playerId))
        print(f"Foul Protection: {playerLabel} has {halfFouls[key]} fouls in this half and will remain on the bench until the next half.")
        return True

    def pullSubs(self, starters, protectedPlayer=None):
        t1ProtectedPlayerId = t1ProtectedSlot = t1ProtectedSourceSlot = None
        t2ProtectedPlayerId = t2ProtectedSlot = t2ProtectedSourceSlot = None
        if protectedPlayer is not None:
            pid = int(protectedPlayer["ID"])
            t1slot = getOnCourtSlot(protectedPlayer, self.t1onCourt)
            t2slot = getOnCourtSlot(protectedPlayer, self.t2onCourt)
            if t1slot is not None:
                t1ProtectedPlayerId = pid; t1ProtectedSlot = t1slot; t1ProtectedSourceSlot = protectedPlayer.get("lineup_source_slot")
            elif t2slot is not None:
                t2ProtectedPlayerId = pid; t2ProtectedSlot = t2slot; t2ProtectedSourceSlot = protectedPlayer.get("lineup_source_slot")
            else:
                raise ValueError(f"Protected player ID {pid} is not currently on the court.")
        t1Excl = set(self.t1FouledOutPlayerIds); t2Excl = set(self.t2FouledOutPlayerIds)
        if self.foulProtectionActive():
            t1Excl.update(self.t1FoulProtectionBench); t2Excl.update(self.t2FoulProtectionBench)
        newT1, newT2, newParams = setLineups(t1Players=self.t1Players, t2Players=self.t2Players, t1RosterById=self.t1RosterById, t2RosterById=self.t2RosterById, t1Probabilities=self.t1Probabilities, t2Probabilities=self.t2Probabilities, HCAAdj=self.HCAAdj, forceStarters=starters, t1ExcludedPlayerIds=t1Excl, t2ExcludedPlayerIds=t2Excl, t1ProtectedPlayerId=t1ProtectedPlayerId, t1ProtectedSlot=t1ProtectedSlot, t2ProtectedPlayerId=t2ProtectedPlayerId, t2ProtectedSlot=t2ProtectedSlot, t1ProtectedSourceSlot=t1ProtectedSourceSlot, t2ProtectedSourceSlot=t2ProtectedSourceSlot)
        newParams["t1OffensiveRebound"] = max(0.0, min(1.0, newParams["t1OffensiveRebound"] + self.t1FormationOffensiveReboundAdjustment))
        newParams["t2OffensiveRebound"] = max(0.0, min(1.0, newParams["t2OffensiveRebound"] + self.t2FormationOffensiveReboundAdjustment))
        return newT1, newT2, newParams

    def applySubs(self, starters, protectedPlayer=None):
        previousLineups = (
            (self.t1, getattr(self, "t1onCourt", {}), self.t1team_df),
            (self.t2, getattr(self, "t2onCourt", {}), self.t2team_df),
        )
        self.t1onCourt, self.t2onCourt, self.lineupParameters = self.pullSubs(
            starters, protectedPlayer
        )
        for team, previousLineup, team_df in previousLineups:
            teamId = int(team_df["id"].iloc[0])
            newLineup = self.t1onCourt if team == self.t1 else self.t2onCourt
            for playerId, replacedId in pair_substitutions(previousLineup, newLineup):
                self.pbp.append(
                    self.make_play(
                        substitution,
                        no_outcome,
                        team_id=teamId,
                        substitute_id=playerId,
                        substituted_id=replacedId,
                    )
                )

    def pullFreeThrowSubs(self, protectedPlayer):
        self.applySubs(False, protectedPlayer)
        print(self.t1 + " Free Throw Subs:")
        for p in self.t1onCourt.values(): print(p["position"] + " " + p["first_name"] + " " + p["last_name"])
        print(self.t2 + " Free Throw Subs:")
        for p in self.t2onCourt.values(): print(p["position"] + " " + p["first_name"] + " " + p["last_name"])

    def adjustMomentum(self, momentumTeam, momentumAmount, momentumReason):
        old = self.momentum
        self.momentum = max(-1.0, min(1.0, self.momentum + (-1.0 if momentumTeam == self.t1 else 1.0) * float(momentumAmount)))
        print(f"Momentum event: {momentumReason} | {old:+.2f} -> {self.momentum:+.2f}")

    def dampenMomentum(self, momentumRetention, momentumReason):
        old = self.momentum
        self.momentum = max(-1.0, min(1.0, self.momentum * float(momentumRetention)))
        print(f"Momentum slowdown: {momentumReason} | {old:+.2f} -> {self.momentum:+.2f}")

    def printMomentumMeter(self):
        pos = max(0, min(10, int(round((self.momentum + 1.0) * 5.0))))
        slots = ["-"] * 11; slots[pos] = "x"
        if self.momentum < 0: team, strength = self.t1, abs(self.momentum)
        elif self.momentum > 0: team, strength = self.t2, self.momentum
        else: team, strength = "Neutral", 0.0
        print(self.t1 + " |" + "".join(slots) + "| " + self.t2 + f"  Momentum: {team} {strength:.0%}")

    def getMomentumAdjustedRating(self, staminaAdjustedRating, ratingTeam):
        if ratingTeam == self.t1: strength = max(0.0, -self.momentum)
        elif ratingTeam == self.t2: strength = max(0.0, self.momentum)
        else: strength = 0.0
        bonus = strength * momentumMaximumAttributeBonus; modifier = 1.0 + bonus
        return float(staminaAdjustedRating) * modifier, strength, bonus, modifier

    def addPlayingTime(self, elapsedTime):
        elapsedTime = round(float(elapsedTime), 2)
        if elapsedTime <= 0: return
        for p in self.t1onCourt.values(): self.t1stats.at[p["id"].item(), "MP"] += elapsedTime
        for p in self.t2onCourt.values(): self.t2stats.at[p["id"].item(), "MP"] += elapsedTime

    def applyFatigueRecovery(self, recoveryAmount, recoveryLabel):
        recoveryAmount = max(0.0, float(recoveryAmount))
        for pid in self.t1RecoveryMinutes:
            sid = self.t1RosterById.at[pid, "id"]; mp = float(self.t1stats.at[sid, "MP"]) / 60
            self.t1RecoveryMinutes[pid] = min(mp, self.t1RecoveryMinutes[pid] + recoveryAmount)
        for pid in self.t2RecoveryMinutes:
            sid = self.t2RosterById.at[pid, "id"]; mp = float(self.t2stats.at[sid, "MP"]) / 60
            self.t2RecoveryMinutes[pid] = min(mp, self.t2RecoveryMinutes[pid] + recoveryAmount)
        print(f"{recoveryLabel}: all players receive up to {recoveryAmount:.2f} minutes of fatigue recovery.")

    def getPlayerRecoveryMinutes(self, player):
        pid = int(player["ID"])
        if pid in self.t1RecoveryMinutes: return self.t1RecoveryMinutes[pid]
        if pid in self.t2RecoveryMinutes: return self.t2RecoveryMinutes[pid]
        return 0.0

    def getPlayerExhaustionPercent(self, player, team):
        sid = int(player["id"])
        mp = float((self.t1stats if team == self.t1 else self.t2stats).at[sid, "MP"]) / 60
        recovery = self.getPlayerRecoveryMinutes(player)
        return max(0.0, (mp - recovery) / max(1.0, float(player["stamina"])) * 100)

    def completeMediaTimeout(self, label, protectedPlayer=None, timeoutTeamId=None):
        print(f"{label} at {int(self.currTime // 60):02d}:{self.currTime % 60:04.1f}.")
        self.pbp.append(
            self.make_play(
                timeout,
                media_timeout,
                team_id=timeoutTeamId if timeoutTeamId is not None else 0,
            )
        )
        self.applyFatigueRecovery(mediaTimeoutRecoveryMinutes, "Media-timeout breather")
        self.dampenMomentum(momentumMediaTimeoutRetention, "media timeout")
        self.applySubs(False, protectedPlayer)
        self.teamTimeoutBlockedUntilLiveAction = True
        print(self.t1 + " Media Timeout Subs:")
        for p in self.t1onCourt.values(): print(p["position"] + " " + p["first_name"] + " " + p["last_name"])
        print(self.t2 + " Media Timeout Subs:")
        for p in self.t2onCourt.values(): print(p["position"] + " " + p["first_name"] + " " + p["last_name"])

    def convertTeamTimeoutToMediaTimeout(self, protectedPlayer=None, timeoutTeam=None):
        if self.period > self.periodPerGame: return False
        timeoutTeamId = None
        if timeoutTeam in (self.t1, self.t2):
            timeoutTeamId = int((self.t1team_df if timeoutTeam == self.t1 else self.t2team_df)["id"].iloc[0])
        if self.league == "CBB":
            for mark in self.cbbMediaTimeoutMarks:
                if mark not in self.cbbMediaTimeoutsTaken[self.period] and mark <= self.currTime <= mark + 30:
                    self.cbbMediaTimeoutsTaken[self.period].add(mark)
                    self.completeMediaTimeout(f"Under-{int(mark / 60)} media timeout converted from a team timeout", protectedPlayer, timeoutTeamId)
                    return True
        else:
            if self.nbaTimeoutEventsByPeriod[self.period] == 0 and 420 <= self.currTime <= 450:
                self.nbaTimeoutEventsByPeriod[self.period] = 1
                self.completeMediaTimeout("Under-7 mandatory media timeout converted from a team timeout", protectedPlayer, timeoutTeamId); return True
            if self.nbaTimeoutEventsByPeriod[self.period] == 1 and 180 <= self.currTime <= 210:
                self.nbaTimeoutEventsByPeriod[self.period] = 2
                self.completeMediaTimeout("Under-3 mandatory media timeout converted from a team timeout", protectedPlayer, timeoutTeamId); return True
        return False

    def checkMediaTimeout(self, protectedPlayer=None):
        if self.period > self.periodPerGame: return False
        label = None
        if self.league == "CBB":
            for mark in self.cbbMediaTimeoutMarks:
                if self.currTime < mark and mark not in self.cbbMediaTimeoutsTaken[self.period]:
                    self.cbbMediaTimeoutsTaken[self.period].add(mark); label = f"Under-{int(mark / 60)} media timeout"; break
        else:
            if self.currTime < 420 and self.nbaTimeoutEventsByPeriod[self.period] == 0:
                self.nbaTimeoutEventsByPeriod[self.period] += 1; label = "Under-7 mandatory media timeout"
            elif self.currTime < 180 and self.nbaTimeoutEventsByPeriod[self.period] == 1:
                self.nbaTimeoutEventsByPeriod[self.period] += 1; label = "Under-3 mandatory media timeout"
        if label is None: return False
        self.completeMediaTimeout(label, protectedPlayer); return True

    def getTeamTimeoutConditions(self, timeoutTeam):
        gp = self.t1TimeoutGameplan if timeoutTeam == self.t1 else self.t2TimeoutGameplan
        onCourt = self.t1onCourt if timeoutTeam == self.t1 else self.t2onCourt
        oppPts = self.t2pts if timeoutTeam == self.t1 else self.t1pts
        myPts = self.t1pts if timeoutTeam == self.t1 else self.t2pts
        lead = oppPts - myPts
        t2a = gp["trigger2_enabled"] == 1 and lead >= gp["trigger2_value"]
        mp = next((p for p in onCourt.values() if int(p["id"]) == gp["trigger3_value"]), None)
        me = self.getPlayerExhaustionPercent(mp, timeoutTeam) if mp else 0.0
        t3a = gp["trigger3_enabled"] == 1 and mp is not None and me >= gp["trigger3_exhaustion"]
        ae = sum(self.getPlayerExhaustionPercent(p, timeoutTeam) for p in onCourt.values()) / max(1, len(onCourt))
        t4a = gp["trigger4_enabled"] == 1 and ae >= gp["trigger4_value"]
        conditions = {"trigger2": t2a, "trigger3": t3a, "trigger4": t4a}
        details = {"trigger2": f"opponent lead {lead} points (threshold {gp['trigger2_value']})", "trigger3": f"designated player exhaustion {me:.1f}% (threshold {gp['trigger3_exhaustion']}%)", "trigger4": f"on-court average exhaustion {ae:.1f}% (threshold {gp['trigger4_value']}%)"}
        return conditions, details

    def checkTeamTimeout(self, priorityTeam, allowNonPossession=False, protectedPlayer=None):
        if priorityTeam not in (self.t1, self.t2): return False
        if self.teamTimeoutBlockedUntilLiveAction:
            self.teamTimeoutBlockedUntilLiveAction = False; return False
        candidates = [priorityTeam]
        if allowNonPossession: candidates.append(self.t2 if priorityTeam == self.t1 else self.t1)
        for team in candidates:
            conditions, details = self.getTeamTimeoutConditions(team)
            for name, active in conditions.items():
                if not active: self.teamTimeoutTriggerLatches[team][name] = False
            reasons = [details[name] for name, active in conditions.items() if active and not self.teamTimeoutTriggerLatches[team][name]]
            if self.teamTimeoutsRemaining[team] <= 0 or not reasons: continue
            gp = self.t1TimeoutGameplan if team == self.t1 else self.t2TimeoutGameplan
            preserve = gp["preserve"] == 1 and self.teamTimeoutsRemaining[team] == 1 and not (self.period > self.periodPerGame or (self.period == self.periodPerGame and self.currTime <= 120))
            if preserve:
                if not self.teamTimeoutPreservationAnnounced[team]:
                    print(f"{team} preserves its final timeout for the last two minutes. Automatic timeout triggers are temporarily blocked.")
                    self.teamTimeoutPreservationAnnounced[team] = True
                continue
            if self.teamTimeoutPreservationAnnounced[team]:
                print(f"{team} timeout preservation restriction has been removed."); self.teamTimeoutPreservationAnnounced[team] = False
            for name, active in conditions.items():
                if active: self.teamTimeoutTriggerLatches[team][name] = True
            print(f"{team} requests a timeout: " + "; ".join(reasons) + ".")
            if self.convertTeamTimeoutToMediaTimeout(protectedPlayer, team):
                print(f"The timeout is charged as a media timeout. {team} keeps all {self.teamTimeoutsRemaining[team]} team timeouts."); return True
            self.teamTimeoutsRemaining[team] -= 1
            print(f"{team} TEAM TIMEOUT at {int(self.currTime // 60):02d}:{self.currTime % 60:04.1f}. Timeouts remaining: {self.teamTimeoutsRemaining[team]}.")
            timeoutTeamId = int((self.t1team_df if team == self.t1 else self.t2team_df)["id"].iloc[0])
            self.pbp.append(
                self.make_play(timeout, team_timeout, team_id=timeoutTeamId)
            )
            self.applyFatigueRecovery(teamTimeoutRecoveryMinutes, f"{team} team-timeout breather")
            self.dampenMomentum(momentumTeamTimeoutRetention, f"{team} team timeout")
            self.applySubs(False, protectedPlayer)
            self.teamTimeoutBlockedUntilLiveAction = True
            if self.league != "CBB" and self.period <= self.periodPerGame: self.nbaTimeoutEventsByPeriod[self.period] = 2
            print(self.t1 + " Team Timeout Subs:")
            for p in self.t1onCourt.values(): print(p["position"] + " " + p["first_name"] + " " + p["last_name"])
            print(self.t2 + " Team Timeout Subs:")
            for p in self.t2onCourt.values(): print(p["position"] + " " + p["first_name"] + " " + p["last_name"])
            return True
        return False

    def checkTimeoutStoppage(self, priorityTeam, allowNonPossession=False, protectedPlayer=None):
        if self.checkMediaTimeout(protectedPlayer): return True
        return self.checkTeamTimeout(priorityTeam, allowNonPossession, protectedPlayer)

    def updateLargestLeads(self):
        t1_lead = self.t1pts - self.t2pts
        if t1_lead > self.t1LargestLead:
            self.t1LargestLead = t1_lead
        t2_lead = self.t2pts - self.t1pts
        if t2_lead > self.t2LargestLead:
            self.t2LargestLead = t2_lead

    def to_player_stats_dtos(self):
        result = []
        for stats_df, fouled_out_ids in (
            (self.t1stats, self.t1FouledOutPlayerIds),
            (self.t2stats, self.t2FouledOutPlayerIds),
        ):
            for pid, row in stats_df[stats_df["MP"] > 0].iterrows():
                dto = PlayerStatsDTO()
                dto.PlayerID = int(pid)
                dto.MatchType = self.league
                dto.Minutes = int(round(float(row["MP"])))
                fgm = int(row["Ins Shot Made"]) + int(row["Mid Shot Made"]) + int(row["3PT Shot Made"])
                fga = int(row["Ins Shot Att"]) + int(row["Mid Shot Att"]) + int(row["3PT Shot Att"])
                dto.FGM = fgm
                dto.FGA = fga
                dto.FGPercent = round(fgm / fga, 4) if fga > 0 else 0.0
                dto.ThreePointsMade = int(row["3PT Shot Made"])
                dto.ThreePointAttempts = int(row["3PT Shot Att"])
                dto.ThreePointPercent = round(dto.ThreePointsMade / dto.ThreePointAttempts, 4) if dto.ThreePointAttempts > 0 else 0.0
                dto.FTM = int(row["FT Shot Made"])
                dto.FTA = int(row["FT Shot Att"])
                dto.FTPercent = round(dto.FTM / dto.FTA, 4) if dto.FTA > 0 else 0.0
                dto.Points = int(row["Pts"])
                dto.OffRebounds = int(row["OREB"])
                dto.DefRebounds = int(row["DREB"])
                dto.TotalRebounds = dto.OffRebounds + dto.DefRebounds
                dto.Assists = int(row["Assist"])
                dto.Steals = int(row["Stl"])
                dto.Blocks = int(row["Blk"])
                dto.Turnovers = int(row["TO"])
                dto.Fouls = int(row["Foul"])
                dto.FouledOut = int(pid) in fouled_out_ids
                result.append(dto)
        return result

    def make_play(self, event_id, outcome_id, elapsed=0,
                  ball_carrier=None, defender=None,
                  blocking_id=0, stealing_id=0, fouling_id=0, passed_id=0,
                  next_x=0, next_y=0, team_id=None, substitute_id=0, substituted_id=0):
        x, y = self.courtPos if isinstance(self.courtPos, tuple) else (0, 0)
        if team_id is None:
            if self.possTeam == self.t1:
                team_id = int(self.t1team_df["id"].iloc[0])
            elif self.possTeam == self.t2:
                team_id = int(self.t2team_df["id"].iloc[0])
            else:
                team_id = 0
        carrier = ball_carrier if ball_carrier is not None else (
            self.possPlayer if isinstance(self.possPlayer, pd.Series) else None
        )
        return BasePlayByPlay(
            GameID=self.gid,
            Quarter=self.period,
            TimeOnClock=int(self.currTime),
            ShotClock=int(self.currShotClock),
            SecondsConsumed=int(round(elapsed)),
            HomeTeamScore=self.t1pts,
            AwayTeamScore=self.t2pts,
            TeamID=team_id,
            BallCarrierID=int(carrier["ID"]) if carrier is not None else 0,
            AssistingPlayerID=int(self.assistPlayer["ID"]) if self.assistPlayer is not None else 0,
            PassedPlayerID=passed_id,
            SubstitutePlayerID=substitute_id,
            SubstitutedPlayerID=substituted_id,
            DefenderID=int(defender["ID"]) if defender is not None else 0,
            BlockingPlayerID=blocking_id,
            StealingPlayerID=stealing_id,
            FoulingPlayerID=fouling_id,
            HomeOffensiveSystem=self.t1OffensiveFormation,
            HomeDefensiveSystem=self.t1DefensiveFormation,
            AwayOffensiveSystem=self.t2OffensiveFormation,
            AwayDefensiveSystem=self.t2DefensiveFormation,
            EventID=event_id,
            OutcomeID=outcome_id,
            XAxis=x,
            YAxis=y,
            NextXAxis=next_x,
            NextYAxis=next_y,
        )

    def to_team_stats_dto(self, is_home):
        stats_df = self.t1stats if is_home else self.t2stats
        teamscore = self.t1teamscore if is_home else self.t2teamscore
        team = self.t1 if is_home else self.t2
        dto = TeamStatsDTO()
        fgm = int(stats_df["Ins Shot Made"].sum()) + int(stats_df["Mid Shot Made"].sum()) + int(stats_df["3PT Shot Made"].sum())
        fga = int(stats_df["Ins Shot Att"].sum()) + int(stats_df["Mid Shot Att"].sum()) + int(stats_df["3PT Shot Att"].sum())
        dto.FGM = fgm
        dto.FGA = fga
        dto.FGPercent = round(fgm / fga, 4) if fga > 0 else 0.0
        dto.ThreePointsMade = int(stats_df["3PT Shot Made"].sum())
        dto.ThreePointAttempts = int(stats_df["3PT Shot Att"].sum())
        dto.ThreePointPercent = round(dto.ThreePointsMade / dto.ThreePointAttempts, 4) if dto.ThreePointAttempts > 0 else 0.0
        dto.FTM = int(stats_df["FT Shot Made"].sum())
        dto.FTA = int(stats_df["FT Shot Att"].sum())
        dto.FTPercent = round(dto.FTM / dto.FTA, 4) if dto.FTA > 0 else 0.0
        dto.Points = int(stats_df["Pts"].sum())
        dto.OffRebounds = int(stats_df["OREB"].sum())
        dto.DefRebounds = int(stats_df["DREB"].sum())
        dto.Rebounds = dto.OffRebounds + dto.DefRebounds
        dto.Assists = int(stats_df["Assist"].sum())
        dto.Steals = int(stats_df["Stl"].sum())
        dto.Blocks = int(stats_df["Blk"].sum())
        dto.TotalTurnovers = int(stats_df["TO"].sum())
        dto.Fouls = int(stats_df["Foul"].sum())
        dto.Possessions = self.teamPossessions[team]
        dto.LargestLead = self.t1LargestLead if is_home else self.t2LargestLead
        sc = teamscore.iloc[0]
        if self.league == "CBB":
            dto.FirstHalfScore = int(sc["P1"])
            dto.SecondHalfScore = int(sc["P2"])
        else:
            dto.FirstHalfScore = int(sc["P1"])
            dto.SecondQuarterScore = int(sc["P2"])
            dto.SecondHalfScore = int(sc["P3"])
            dto.FourthQuarterScore = int(sc["P4"])
        dto.OvertimeScore = int(sc["OT"])
        return dto
