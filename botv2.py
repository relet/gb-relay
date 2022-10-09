#!/usr/bin/env python

import sys
import time
import hashlib
import json
from turtle import update
import requests
import random

settings = json.loads(open(".settings", "r").read())

BRAIN_URL = settings.get("brain_url")
SECRET = settings.get("brain_secret")

GAME_ID = "13726"
PACKET_ID = 0
SESSION_ID = ""

ENTITIES = {}

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
    return rdata

def handle_response_login(rdata):
    global SESSION_ID
    global ENTITIES

    open("logindata", "w").write(json.dumps(rdata, indent=2))

    entities = rdata.get("entities")
    player_name = rdata.get("playerName")
    team_id = None
    player_id = rdata.get("id")

    for ent in entities:
        entType = ent.get("entityType")
        ENTITIES[entType]=ent
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
    req = gen_data(message)
    headers = get_headers(req)
    r = requests.post(BRAIN_URL, headers=headers, json=req)
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
            print("REQUEST")
            print(json.dumps(req, indent=2))
            print()
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
        "countryCode": "US",
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
        "forceCreate": True,
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
          "text": "message"
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

def get_team_chat(teamId):
    script_message = {
        "data": {
            "channelId": GAME_ID + ":gr:" + teamId,
            "maxReturn": 100,
        },
        "operation": "GET_RECENT_CHAT_MESSAGES",
        "service": "chat"
    }
    responses = send_request(script_message)
    return responses[0].get("messages")

def channel_connect(teamId):
    script_message = {
        "data": {
            "channelId": GAME_ID + ":gr:" + teamId,
            "maxReturn": 1000
        },
        "operation": "CHANNEL_CONNECT",
        "service": "chat"
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

    # I think we cannot say anything if the player doesn't have a team
    if not teamId:
        return False

    members = get_team_members(teamId)
    for memberId, memberData in members.items():
        if memberId == playerId:
            return memberData.get("customData",{}).get("online", False)

def sell_card(cardId, cardType, count):
    script_message = {
        "data": {
            "scriptData": {
                "CARD_ID": cardId,
                "CARD_TYPE": cardType,
                "COUNT": count,
            },
            "scriptName": "teams/TRADE_SELL_CARD",
        },
        "operation": "RUN",
        "service": "script"
    }
    responses = send_request(script_message)
    return responses[0]

def buy_card(cardId, cardType, count):
    script_message = {
        "data": {
            "scriptData": {
                "CARD_ID": cardId,
                "CARD_TYPE": cardType,
                "COUNT": count,
            },
            "scriptName": "teams/TRADE_BUY_CARD",
        },
        "operation": "RUN",
        "service": "script"
    }
    responses = send_request(script_message)
    return responses[0]

def get_card_pool(teamid):
    card_pool = {'hat':{}, 'golfer': {}}
    page = 1
    more_results = True
    while(more_results):
        script_message = {
                 "data": {
                    "context": {
                       "pagination": {
                          "pageNumber": page,
                          "rowsPerPage": 50
                       },
                       "searchCriteria": {
                          "entityType": "TRADING_CARD",
                          "groupId": teamid
                       },
                       "sortCriteria": {
                          "data.id": 1
                       }
                    }
                 },
                 "operation": "READ_GROUP_ENTITIES_PAGE",
                 "service": "group"
              }
        responses = send_request(script_message)
        page += 1
        for response in responses:
            if 'results' in response:
                if 'moreAfter' in response['results']:
                    if not response['results']['moreAfter']:
                        more_results = False
                if 'items' in response['results']:
                    for carditem in response['results']['items']:
                        if int(carditem['data']['id']) in card_pool[carditem['data']['type']]:
                            print('this card was already found')
                        card_pool[carditem['data']['type']][int(carditem['data']['id'])] = int(carditem['data']['count'])
    return responses, card_pool

def open_pack(packId):
    request = {
      "data": {
        "scriptData": {
          "slot_num": packId
        },
        "scriptName": "packs/OPEN_SLOT_PACK"
      },
      "operation": "RUN",
      "service": "script"
    }
    responses = send_request(request)
    return responses

def get_player_by_friend_code(friendCode):
    request = {
      "data": {
        "scriptData": {
          "fast_friend_token": "",
          "friend_code": friendCode
        },
        "scriptName": "friends/FRIEND_ADD_FRIEND"
      },
      "operation": "RUN",
      "service": "script"
    }
    response = send_request(request)
    print(json.dumps(response, indent=2))
    if response[0]["success"] == True:
        return response[0].get("response").get("targetPlayer")
    else:
        return None

def get_player_info(playerId):
    request = {
      "data": {
        "scriptData": {
          "player_id": playerId
        },
        "scriptName": "events/GET_PLAYER_INFO"
      },
      "operation": "RUN",
      "service": "script"
    }
    return send_request(request)

def search_teams(search):
    request = {
      "data": {
        "scriptData": {
          "COUNTRY": "DE",
          "NAME": search,
          "REQUIRED_TROPHIES": -1
        },
        "scriptName": "teams/TEAM_SEARCH_EVENT"
      },
      "operation": "RUN",
      "service": "script"
    }
    return send_request(request)

def download_file(assetId):
    request = {
        "data": {
            "filename": assetId,
            "folderPath": "/"
        },
        "operation": "GET_FILE_INFO_SIMPLE",
        "service": "globalFileV3"
    }
    reply = send_request(request)

    url = reply[0].get("fileDetails").get("url")
    with requests.get(url) as r:
        with open(assetId+".zip", 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
    return True

if __name__=="__main__":
    # EXAMLE USAGE
    # LOGIN
    player_data = login(sys.argv[1], sys.argv[2])
    print(json.dumps(player_data, indent=2))
    # GET MEMBERS OF PLAYER TEAM
    teamId = player_data.get("teamId")
    teammates = get_team_members(teamId)
    print(json.dumps(teammates, indent=2))
    sys.exit(1)
