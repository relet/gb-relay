#!/usr/bin/env python

import sys
import hashlib
import json
import requests

settings = json.loads(open(".settings", "r").read())

BRAIN_URL = settings.get("brain_url")
SECRET = settings.get("brain_secret")

GAME_ID = "13726"
PACKET_ID = 0
SESSION_ID = ""

headers = {
  "Host": "api.braincloudservers.com",
  "Accept": "*/*",
  "X-APPID": GAME_ID,
  "Content-Type": "application/json",
}

basedata = {
  "gameId": GAME_ID,
  "messages": [],
  "packetId": PACKET_ID,
  "sessionId": SESSION_ID
}

def gen_data(message):
    global PACKET_ID
    global SESSION_ID
    data = json.loads(json.dumps(basedata))
    data['messages'].append(message)
    data['sessionId'] = SESSION_ID
    data['packetId'] = PACKET_ID
    return data

def get_headers(data):
    signature = hashlib.md5(bytearray(json.dumps(data)+SECRET, "utf-8")).hexdigest()
    my_headers = json.loads(json.dumps(headers))
    my_headers["X-SIG"] = signature.upper()
    return my_headers

def dump_response(rdata):
    #print(json.dumps(rdata, indent=2))
    return rdata

def handle_response_login(rdata):
    global SESSION_ID

    entities = rdata.get("entities")
    player_name = rdata.get("playerName")
    team_id = None
    player_id = rdata.get("id")

    for ent in entities:
        entType = ent.get("entityType")
        if entType == "public":
            team_id = ent.get("data").get("team_id")

    SESSION_ID = rdata.get("sessionId")
    return {
        "sessionId": SESSION_ID,
        "playerId": player_id,
        "name": player_name,
        "teamId": team_id
    }

def send_request(message, response_handler=dump_response):
    global PACKET_ID
    data = gen_data(message)
    headers = get_headers(data)
    r = requests.post(BRAIN_URL, headers=headers, json=data)
    PACKET_ID += 1

    if (r.status_code != 200):
        print("Error: Authentication failed")
        print(r.text)
        return None

    data=r.json()

    responses = data["responses"]

    handled_responses = []
    for r in responses:
        if (r.get("status") != 200):
            print("Error: Request failed")
            print(r.get("status_message"))
            sys.exit(1)

        rdata = r.get("data")
        handled_responses.append(response_handler(rdata))
    return handled_responses


def login(email, password):
    login_message = {
      "data": {
        "anonymousId": "",
        "authenticationToken": password,
        "authenticationType": "Email",
        "clientLib": "cpp",
        "clientLibVersion": "4.12.2",
        "countryCode": "GB",
        "externalId": email,
        "extraJson": {
          "client_version": 209,
          "game_version": 1,
          "gamesparks_auth_data": {
#            "device_id": "f0d02d31-42bb-4d7e-a426-a20e9a0cacb8",
            "device_os": "Android",
            "email": email,
            "password": password,
          }
        },
        "forceCreate": False,
        "gameId": "13726",
        "gameVersion": "3.0.1 (414)",
        "languageCode": "",
        "profileId": "",
        "releasePlatform": "ANG",
        "timeZoneOffset": 0
      },
      "operation": "AUTHENTICATE",
      "service": "authenticationV2"
      }
    responses = send_request(login_message, handle_response_login)
    #responses = send_request(login_message)
    return responses[0]

def send_chat_message(teamId, message):
    chat_message = {
      "data": {
        "channelId": GAME_ID + ":gr:" + teamId,
        "content": {
          "message": {
            "msg": message,
            "type": "chat"
          },
          "text": message
        },
        "recordInHistory": True
      },
      "operation": "POST_CHAT_MESSAGE",
      "service": "chat"
    }

    responses = send_request(chat_message)
    return responses[0]

def promote_player(teamId, playerId):
    script_message =       {
      "data": {
        "scriptData": {
            "player_id": playerId,
        },
        "scriptName": "teams/PROMOTE_PLAYER",
      },
      "operation": "RUN",
      "service": "script"
    }
    responses = send_request(script_message)
    return responses[0]

def demote_player(teamId, playerId):
    script_message =       {
      "data": {
        "scriptData": {
            "player_id": playerId,
        },
        "scriptName": "teams/DEMOTE_PLAYER",
      },
      "operation": "RUN",
      "service": "script"
    }
    responses = send_request(script_message)
    return responses[0]

def boot_player(teamId, playerId):
    script_message =       {
      "data": {
        "scriptData": {
            "player_id": playerId,
        },
        "scriptName": "teams/BOOT_PLAYER",
      },
      "operation": "RUN",
      "service": "script"
    }
    responses = send_request(script_message)
    return responses[0]

def get_team_members(teamId):
    script_message =       {
      "data": {
        "groupId": teamId,
      },
      "operation": "READ_GROUP_MEMBERS",
      "service": "group"
    }
    responses = send_request(script_message)
    return responses[0]

def get_player_info(playerId):
    script_message = {
        "data": {
            "scriptData": {
                "player_id": playerId,
            },
            "scriptName": "events/GET_PLAYER_INFO",
        },
        "operation": "RUN",
        "service": "script"
    }
    responses = send_request(script_message)
    return responses[0]

def is_player_online(playerId, teamId=None):
    if not teamId:
        data = get_player_info(playerId)
        teamId = data.get("response", {}).get("player_public_data", {}).get("data", {}).get("team_id")

    # I think we cannot tell if the player doesn't have a team
    if not teamId:
        return False

    members = get_team_members(teamId)
    for memberId, memberData in members.items():
        if memberId == playerId:
            return memberData.get("customData",{}).get("online", False)

if __name__=="__main__":
  # EXAMLE USAGE
  # LOGIN
  player_data = login(settings["email"], settings["password"])
  print(json.dumps(player_data, indent=2))

  # SEND A REQUEST
  print(json.dumps(is_player_online(player_data.get("playerId")), indent=2))