#!/usr/bin/env python3

import asyncio
import base64
import discord
from discord.ext import tasks, commands
from discord_slash import SlashCommand, SlashContext
from discord_slash.utils.manage_commands import create_option
import json
import hmac
import hashlib
import logging
import websockets
import requests
import time
import sys

command_token="!"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(name="gb-relay")

settings = json.load(open('.settings','r'))
state = json.load(open('.state','r'))

gb_entryURL = settings.get('entry_url')
hmac_key = bytes(settings.get('hmac_key',''), 'utf-8')
admins = settings.get('admins')

def keep_state():
    fd = open('.state','w')
    fd.write(json.dumps(state, indent=2))
    fd.close()
    logger.info("State stored.")

client = commands.Bot(command_prefix='%')
#client = discord.Client(intents=discord.Intents.default())
slash = SlashCommand(client, sync_commands=True)

@client.event
async def on_ready():
    logger.info('We have logged in as {0.user}'.format(client))
    check_chats.start()

@client.event
async def on_message(message):
    return

@slash.slash(name = "reply",
             description = "Send a reply to the ingame chat.",
             options = [
                 create_option(
                     name = "reply",
                     description = "the message you want to send.",
                     option_type = 3,
                     required = True
                 )
             ],
             guild_ids = settings.get('guild_ids',[]))
async def reply(ctx, reply):
    await ctx.defer()
    await send_reply(ctx.channel.id, ctx.author.display_name, reply)
    await ctx.send(reply+"\nYour reply was queued and will be sent during the next relay.", delete_after=60.0)

@slash.slash(name = "announce",
             description = "Send a reply to the ingame chat of all teams.",
             options = [
                 create_option(
                     name = "message",
                     description = "the message you want to send.",
                     option_type = 3,
                     required = True
                 )
             ],
             guild_ids = settings.get('guild_ids',[]))
async def announce(ctx, message):
    await ctx.defer()
    for chat in settings.get('chats',[]):
        if chat.get('read_only'):
            continue
        await send_reply(chat.get('channel'), ctx.author.display_name, message)
    await ctx.send("Your announcement was queued and will be sent during the next relay.", delete_after=60.0)

@slash.slash(name = "yellowcard",
             description = "Warn and demote a player / issue a yellow card.",
             options = [
                 create_option(
                     name = "player",
                     description = "unique part of the player name, or playerid of the player you want to warn.",
                     option_type = 3,
                     required = True
                 )
             ],
             guild_ids = settings.get('guild_ids',[]))
async def yellowcard(ctx, player):
    if str(ctx.author) not in admins:
        await ctx.send("You are not allowed to issue this command.", delete_after=300.0)
    else:
        await ctx.defer()
        await store_warning(ctx.channel.id, player)
        await ctx.send("Your warning was queued and will be sent during the next relay.", delete_after=60.0)

@slash.slash(name = "redcard",
             description = "Boot and block a player / issue a red card.",
             options = [
                 create_option(
                     name = "player",
                     description = "unique part of the player name, or playerid of the player you want to boot.",
                     option_type = 3,
                     required = True
                 )
             ],
             guild_ids = settings.get('guild_ids',[]))
async def redcard(ctx, player):
    if str(ctx.author) not in admins:
        await ctx.send("You are not allowed to issue this command.", delete_after=300.0)
    else:
        await ctx.defer()
        await store_redcard(ctx.channel.id, player)
        await ctx.send("Your boot request was queued and will be sent during the next relay.", delete_after=60.0)

@slash.slash(name = "boot",
             description = "Boot a player without issuing a block.",
             options = [
                 create_option(
                     name = "player",
                     description = "unique part of the player name, or playerid of the player you want to boot.",
                     option_type = 3,
                     required = True
                 )
             ],
             guild_ids = settings.get('guild_ids',[]))
async def boot(ctx, player):
    if str(ctx.author) not in admins:
        await ctx.send("You are not allowed to issue this command.", delete_after=300.0)
    else:
        await ctx.defer()
        await store_boot(ctx.channel.id, player)
        await ctx.send("Your boot request was queued and will be sent during the next relay.", delete_after=60.0)

# queue messages for delivery
async def send_reply(channel, author, reply):
    await store_event(channel, ":| "+str(author)+" (via discord) |:", reply)
async def store_warning(channel, player):
    await store_event(channel, "yellow", player)
async def store_redcard(channel, player):
    await store_event(channel, "red", player)
async def store_boot(channel, player):
    await store_event(channel, "boot", player)

async def store_event(channel, author, reply):
    state['queued_messages']=state.get('queued_messages',{})
    queue = state.get('queued_messages',{}).get(str(channel),[])
    queue.append((author, str(reply)))
    state['queued_messages'][str(channel)]=queue
    keep_state()

# == server interaction =============================================================

async def connect_as(user, passwd):
    portal = await websockets.connect(gb_entryURL)
    info = json.loads(await portal.recv())

    if not 'connectUrl' in info:
        return False

    sock = await websockets.connect(info.get('connectUrl'), max_size=1_000_000_000)
    info = json.loads(await sock.recv())

    if not 'nonce' in info:
        return False

    await sock.send(json.dumps({
        "@class": ".AuthenticatedConnectRequest",
        "hmac": base64.b64encode(hmac.new(hmac_key, info.get('nonce').encode('utf-8'), hashlib.sha256).digest()).decode('utf-8'),
        "os": "BotOS"
    }))
    info = json.loads(await sock.recv())

    if not 'sessionId' in info:
        return False

    await sock.send(json.dumps({
        "@class": ".AuthenticationRequest",
        "userName": user,
        "password": passwd,
        "scriptData": {"game_version": 9999, "client_version": 99999},
        "requestId": "_auth"}))
    # TODO: detect if login fails

    return sock

async def is_player_online(player_id):
    sock = await connect_as(settings['checker-email'], settings['checker-pass'])
    if not sock:
        logger.error("Cannot log in checker account")
        return
    await sock.send(json.dumps({
        "@class": ".LogEventRequest",
        "player_id": player_id,
        "eventKey": "PLAYER_INFO",
        "requestId": player_id
    }))

    while True:
        info = json.loads(await sock.recv())
        if info.get('requestId')==player_id:
            break
    now = time.time() * 1000

    if not 'scriptData' in info:
        logger.error("Cannot retrieve player info.")

    last_login = info.get('scriptData').get('data').get('last_login')
    delta = now - last_login

    # if login is newer than 5 minutes, this player is online
    # TODO: check if that even works
    return (delta < 250.0)

# welcome or ban people
async def welcome_and_promote(ws, team_id, who, pid):
    redlist = state.get('redlist',[])
    if pid in redlist:
        chatmessage="""{}, Du bist bei uns nicht mehr willkommen. Wenn Du dagegen Einwände hast, rede mit uns auf Discord.""".format(who)
        chatmessage_en="""{}, you are banned from the GoT family. If you want to appeal this decision, talk to us on Discord.""".format(who)
    else:
        chatmessage="""Herzlich willkommen {} im Team! 🥳 Nachfolgend ein paar Regeln und Infos:
1.) In Challenge-Matches spielen wir untereinander (GoT, Golfsrudel und Winterfell) immer auf Unentschieden
2.) Bitte verkaufe regelmäßig Karten
3.) Wir chatten hauptsächlich auf Discord - mach mit auf ogy.de/GoT
4.) Wenn du Fragen hast, lass es uns einfach wissen""".format(who)
        chatmessage_en="""Welcome {} to the team! 🥳 Here are a few rules and notes:
1.) In challenge matches we always tie with our teammates (GoT, Golfsrudel, and Winterfell)
2.) Please sell cards regularly
3.) Most of the chatting happens on our Discord server. Join us on ogy.de/GoT
4.) If you have any questions, just ask away""".format(who)

    await ws.send(json.dumps( {
        "@class": ".SendTeamChatMessageRequest",
        "teamId": team_id,
        "message": json.dumps({"type":"chat", "msg": chatmessage_en}),
        "requestId": "welcome_en"
    }))
    time.sleep(.2)

    await ws.send(json.dumps( {
        "@class": ".SendTeamChatMessageRequest",
        "teamId": team_id,
        "message": json.dumps({"type":"chat", "msg": chatmessage}),
        "requestId": "welcome"
    }))
    time.sleep(.2)

    if pid in redlist:
        action="BOOT_PLAYER"
    else:
        action="PROMOTE_PLAYER"

    time.sleep(1.0)
    await ws.send(json.dumps( {
        "@class": ".LogEventRequest",
        "eventKey": action,
        "player_id": pid,
        "requestId": "boot_or_boost"
    }))

async def warn_and_demote(ws, team_id, who, pid, complaint):
    compl_de = ""
    compl_en = ""
    if complaint:
        compl_de=" von "+complaint
        compl_en=" by "+complaint
    chatmessage="""🟨 Vorsicht {}! Wir spielen in der Challenge gegen Teammitglieder Unentschieden!
Das gilt für GoT, Golfsrudel und Winterfell!
Nach einer Beschwerde{} hast Du jetzt eine gelbe Karte. Bitte entschuldige Dich auf Discord, oder
schenke einem anderen Teammitglied einen Challenge-Sieg um die Warnung abzubauen.""".format(who, compl_de)

    chatmessage_en="""🟨 Careful {}! We tie in challenge matches with teammates (GoT, Golfsrudel and Winterfell)
After a complaint{}, you have been issued a yellow card. Please apologize on Discord, or forfeit
another game against one of your team members to get rid of the warning""".format(who, compl_en)

    await ws.send(json.dumps( {
        "@class": ".SendTeamChatMessageRequest",
        "teamId": auth['teamid'],
        "message": json.dumps({"type":"chat", "msg": chatmessage_en}),
        "requestId": "warn_en"
    }))
    time.sleep(.2)

    await ws.send(json.dumps( {
        "@class": ".SendTeamChatMessageRequest",
        "teamId": auth['teamid'],
        "message": json.dumps({"type":"chat", "msg": chatmessage}),
        "requestId": "warn"
    }))
    time.sleep(.2)

    await ws.send(json.dumps( {
        "@class": ".LogEventRequest",
        "eventKey": "DEMOTE_PLAYER",
        "player_id": pid,
        "requestId": "demote"
    }))

async def boot_and_block(sock, team_id, pid):
    redlist = state.get('redlist',[])
    if not pid in redlist:
        redlist.append(str(pid))
    state['redlist']=redlist
    keep_state()
    await boot_player(sock, team_id, pid)

async def boot_player(sock, team_id, pid):
    await sock.send(json.dumps( {
        "@class": ".LogEventRequest",
        "eventKey": "BOOT_PLAYER",
        "player_id": pid,
        "requestId": "boot"
    }))

async def get_player_by_id_or_string(sock, team_id, search):
    await sock.send(json.dumps({
        "@class": ".LogEventRequest",
        "requestId": "get_team",
        "eventKey": "GET_TEAM_REQUEST",
        "team_id": team_id,
    }))
    while True:
        message = await sock.recv()
        data=json.loads(message)
        if data.get("requestId")=="get_team":
            members = data['scriptData']['members']
            for mem in members:
                mempid = mem['id']
                memname = mem['displayName']
                if search in mempid or search in memname:
                    pid = mempid
                    who = memname
                    return pid, who
            return None, None

# get chat messages
@tasks.loop(seconds=60)
async def check_chats():
    logger.info("Starting background task")
    chats = settings.get('chats')
    for chat in chats:
        if await is_player_online(chat['playerid']):
            continue
        sock = await connect_as(chat['email'], chat['pass'])
        if not sock:
            logger.error("Could not log in as "+chat['name'])
            continue

        team_id = chat['teamid']
        channel = await client.fetch_channel(chat['channel'])
        if not channel:
            logger.error("Could not retrieve channel id "+chat['channel'])
            continue

        messages = state.get('queued_messages',{}).get(str(channel.id),[])
        if not chat.get('read_only'):
          for msg in messages:
            author, reply = msg # or event and player
            if author == "yellow":
                pid, pname = await get_player_by_id_or_string(sock, team_id, reply)
                if not pid:
                    continue
                await warn_and_demote(sock, team_id, pname, pid, "") #TODO: implement complainer
            elif author == "red":
                pid, pname = await get_player_by_id_or_string(sock, team_id, reply)
                if not pid:
                    continue
                await boot_and_block(sock, team_id, pid)
            elif author == "boot":
                pid, pname = await get_player_by_id_or_string(sock, team_id, reply)
                if not pid:
                    continue
                await boot_player(sock, team_id, pid)
            else:
                await sock.send(json.dumps( {
                    "@class": ".SendTeamChatMessageRequest",
                    "teamId": team_id,
                    "message": json.dumps({"type":"chat", "msg": "{}\n{}".format(author, reply)}),
                    "requestId": "reply"
                }))

        state['queued_messages']=state.get('queued_messages',{})
        state['queued_messages'][str(channel.id)]=[]
        keep_state()

        await sock.send(json.dumps({
            "@class": ".ListTeamChatRequest",
            "entryCount": 100,
            "requestId": team_id,
            "teamId": team_id
        }))
        while True:
            data = json.loads(await sock.recv())
            if data.get('requestId')==team_id:
                break
        if not 'messages' in data:
            logger.error("Could not receive messages via "+chat['name'])
            continue

        lines = []

        last_posted_message=state.get('last_posted_message', {})
        newest = last_posted_message.get(team_id,0)

        for line in data['messages']:
            if line['when'] <= newest:
                continue
            lines = lines+[line]
        lines.sort(key=lambda x:x['when'])

        joined={}
        for line in lines:
            chatmsg = json.loads(line['message'])
            postmsg = chatmsg['msg']
            if chatmsg['type']=='leave':
                postmsg = chatmsg['msg']+' left the team.'
                if chatmsg['msg'] in joined:
                    for key, value in dict(joined).items():
                        if value==chatmsg['msg']:
                            del joined[key]
            if chatmsg['type']=='promote':
                postmsg = line['who']+' has promoted '+chatmsg['promoted']+"."
                if chatmsg['promoted'] in joined:
                    #todo: actually just send the message, but don't promote.
                    for key, value in dict(joined).items():
                        if value==chatmsg['promoted']:
                            del joined[key]
            if chatmsg['type']=='demote':
                postmsg = line['who']+' has demoted '+chatmsg['demoted']+"."
            if chatmsg['type']=='boot':
                postmsg = line['who']+' has booted '+chatmsg['booted']+"."
            if chatmsg['type']=='join':
                postmsg = chatmsg['msg']+' joined the team. (player id: '+line['fromId']+')'
                joined[chatmsg['msg']]=line['fromId']
            if chatmsg['type']=='friendly_match':
                continue
                postmsg = line['who']+' started a friendly match.'

            author = line['who']
            #chattime = time.ctime(line['when']/1000) - not that necessary anymore
            to_discord = "**{}**".format(postmsg)

            # use webhooks for impersonation
            temp_webhook = await channel.create_webhook(name = "hook-"+author)
            await temp_webhook.send(to_discord, username=line['who'])
            await temp_webhook.delete()

            if chatmsg['type']=='join':
                 time.sleep(0.1)
                 await channel.send("?playerinfo -id "+line['fromId'])

            last_posted_message[team_id] = line['when']
            state['last_posted_message'] = last_posted_message
            keep_state()

            if not chat.get('read_only'):
              for join in joined.keys():
                logger.info("promoting (or banning)", join, joined[join])
                await welcome_and_promote(sock, team_id, join, joined[join])
    logger.info("Finished background task.")


client.run(settings.get('token'))
