#!/usr/bin/env python3

import asyncio
import base64
from contextlib import suppress
import discord
from discord.ext import tasks, commands
from discord_slash import SlashCommand, SlashContext
from discord_slash.utils.manage_commands import create_option
import enet
import json
import hmac
import hashlib
import logging
import logging.handlers
import websockets
import requests
import time
import sys

import botv2 as bot

def log_setup(settings):
    log_handler = logging.handlers.WatchedFileHandler(settings.get('logfile'))
    formatter = logging.Formatter(
        '%(asctime)s program_name [%(process)d]: %(message)s',
        '%b %d %H:%M:%S')
    formatter.converter = time.gmtime  # if you want UTC time
    log_handler.setFormatter(formatter)
    logger = logging.getLogger()
    logger.addHandler(log_handler)
    logger.setLevel(logging.INFO)

settings = json.load(open('.settings','r'))
state = json.load(open('.state','r'))
teamoverride = json.load(open('teamoverrides.json', 'r'))
fullconfig = json.load(open('cardconfig.json', 'r'))

log_setup(settings)
logger = logging.getLogger(name="gb-relay")

brain_URL = settings.get('brain_url')
brain_secret = bytes(settings.get('brain_secret',''), 'utf-8')

admins = settings.get('admins')
relogins = 0

is_running = False
RATE_LIMIT = 120

def keep_state():
    fd = open('.state','w')
    fd.write(json.dumps(state, indent=2))
    fd.close()
    logger.info("State stored.")

client = commands.Bot(command_prefix='%')
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
    with suppress(Exception):
        await ctx.defer()
    await send_reply(ctx.channel.id, ctx.author.display_name, reply)
    with suppress(Exception):
        await ctx.send(reply+"\nYour reply was queued and will be sent during the next relay.", delete_after=60.0)

@slash.slash(name = "notify",
             description = "Notify a player when they are online.",
             options = [
                 create_option(
                     name = "player",
                     description = "a substring of the player name or id you want to notify",
                     option_type = 3,
                     required = True
                 ),
                 create_option(
                     name = "message",
                     description = "the message you want to send.",
                     option_type = 3,
                     required = True
                 )
             ],
             guild_ids = settings.get('guild_ids',[]))
async def notify(ctx, player, message):
    with suppress(Exception):
        await ctx.defer()
    await send_notify(ctx.channel.id, ctx.author.display_name, player, message)
    with suppress(Exception):
        await ctx.send(message+"\nYour notification was queued and will be sent when the player is online.", delete_after=60.0)

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
    with suppress(Exception):
        await ctx.defer()
    for chat in settings.get('chats',[]):
        if chat.get('read_only'):
            continue
        await send_reply(chat.get('channel'), ctx.author.display_name, message)
    with suppress(Exception):
        await ctx.send(message+"\nYour announcement was queued and will be sent during the next relay.", delete_after=60.0)

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
        with suppress(Exception):
            await ctx.defer()
        await store_warning(ctx.channel.id, player)
        with suppress(Exception):
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
        with suppress(Exception):
            await ctx.defer()
        await store_redcard(ctx.channel.id, player)
        with suppress(Exception):
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
        with suppress(Exception):
            await ctx.defer()
        await store_boot(ctx.channel.id, player)
        with suppress(Exception):
            await ctx.send("Your boot request was queued and will be sent during the next relay.", delete_after=60.0)

# queue messages for delivery
async def send_reply(channel, author, reply):
    await store_event(channel, ":| "+str(author)+" (via discord) |:", reply)
async def send_notify(channel, author, target, message):
    await store_event(channel, "!"+target, ":| "+str(author)+" (via discord, notifying "+target+") |:\n"+message)
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
    return bot.login(user, passwd)
    # do we need to handle the reply?

async def is_player_online(player_id, team_id):
    player_data = connect_as(settings['checker-email'], settings['checker-pass'])
    if not ok:
        logger.error("Cannot log in checker account")
        return

    # todo: check if player is online
    return False

# welcome or ban people
welcomed={}
async def welcome_and_promote(ws, team_id, who, pid):
    if pid in welcomed:
        logger.info("Refusing to welcome {} twice.".format(who))
        return
    welcomed[pid]=True

    redlist = state.get('redlist',[])
    if pid in redlist:
        chatmessage="""{}, Du bist bei uns nicht mehr willkommen. Wenn Du dagegen Einwände hast, rede mit uns auf Discord.""".format(who)
        chatmessage_en="""{}, you are banned from the Wolf🐺Gang family. If you want to appeal this decision, talk to us on Discord.""".format(who)
    else:
        chatmessage="""Herzlich willkommen {} im Team! 🥳 Nachfolgend ein paar Regeln und Infos:
1.) In Challenge-Matches spielen wir in der Wolf🐺Gang-Familie immer auf Unentschieden
2.) Bitte verkaufe regelmäßig Karten
3.) Wir chatten hauptsächlich auf Discord - mach mit auf ogy.de/WG
4.) Wenn du Fragen hast, lass es uns einfach wissen""".format(who)
        chatmessage_en="""Welcome {} to the team! 🥳 Here are a few rules and notes:
1.) In challenge matches we tie with the entire Wolf🐺Gang family.
2.) Please sell cards regularly
3.) Most of the chatting happens on our Discord server. Join us on ogy.de/WG
4.) If you have any questions, just ask away""".format(who)

    bot.send_chat_message(team_id, chatmessage_en)
    bot.send_chat_message(team_id, chatmessage)

    if pid in redlist:
        action="BOOT_PLAYER"
        bot.boot_player(team_id, pid)

async def warn_and_demote(ws, team_id, who, pid, complaint):
    compl_de = ""
    compl_en = ""
    if complaint:
        compl_de=" von "+complaint
        compl_en=" by "+complaint
    chatmessage="""🟨 Vorsicht {}! Wir spielen in der Challenge gegen Teammitglieder der Wolf Gang Unentschieden!
Nach einer Beschwerde{} hast Du jetzt eine gelbe Karte. Bitte entschuldige Dich auf Discord, oder
schenke einem anderen Teammitglied einen Challenge-Sieg um die Warnung abzubauen.""".format(who, compl_de)

    chatmessage_en="""🟨 Careful {}! We tie in challenge matches with teammates in all Wolf Gang teams!
After a complaint{}, you have been issued a yellow card. Please apologize on Discord, or forfeit
another game against one of your team members to get rid of the warning""".format(who, compl_en)

    bot.send_chat_message(team_id, chatmessage_en)
    bot.send_chat_message(team_id, chatmessage)
    bot.demote_player(team_id, pid)
    logger.info("Sent warning")

async def boot_and_block(sock, team_id, pid):
    redlist = state.get('redlist',[])
    if not pid in redlist:
        redlist.append(str(pid))
    state['redlist']=redlist
    keep_state()
    bot.boot_player(team_id, pid)

async def get_player_by_id_or_string(sock, team_id, search):
    team = bot.get_team_members(team_id)
    for playerId, data in team.items():
        if search in playerId or search in data['playerName']:
            return playerId, data['playerName']
    return None, None

# get chat messages
@tasks.loop(seconds=RATE_LIMIT)
async def check_chats():
    global is_running
    global relogins

    if is_running:
        logger.warn("Not starting background task, still running.")
        logger.warn("Instead, we just restart the service. It's about time")
        os.system("systemctl restart gb-relay")
        time.sleep(10)
        logger.warn("Still alive")
        return
    is_running = True
    logger.info("Starting background task")

    chats = settings.get('chats')
    for chat in chats:
        player_info = None
        team_info = None

        logger.info("Checking "+chat['name'])
        team_id = chat['teamid']
        ignore_online = chat.get('ignore_online',0)
        if not ignore_online and await is_player_online(chat['playerid'], team_id):
            continue

        channel = await client.fetch_channel(chat['channel'])
        if not channel:
            logger.error("Could not retrieve channel id "+chat['channel'])
            continue

        # login main account
        player_info = await connect_as(chat['email'], chat['pass'])
        if not player_info['sessionId']:
            logger.error("Could not log in as "+chat['name'])
            continue

        # TODO: gather player data and card packs from script messages
        # fetch player_info

        authenticated = 'playerId' in player_info
        if not authenticated:
            logger.error("Could not authenticate.")
            continue

        messages = state.get('queued_messages',{}).get(str(channel.id),[])
        new_queue = []
        if not chat.get('read_only'):
          for msg in messages:
            author, reply = msg # or event and player
            if author == "yellow":
                pid, pname = await get_player_by_id_or_string(team_id, reply)
                if not pid:
                    await channel.send("Could not find player by string '{}'.".format(reply))
                    continue
                logger.info("Sending warning to "+pname+" "+pid)
                await warn_and_demote(team_id, pname, pid, "") #TODO: implement complainer
            elif author == "red":
                pid, pname = await get_player_by_id_or_string(team_id, reply)
                if not pid:
                    await channel.send("Could not find player by string '{}'.".format(reply))
                    continue
                await boot_and_block(team_id, pid)
            elif author == "boot":
                pid, pname = await get_player_by_id_or_string(sock, team_id, reply)
                if not pid:
                    await channel.send("Could not find player by string '{}'.".format(reply))
                    continue
                await boot_player(sock, team_id, pid)
            elif author[0] == "!":
                pid, pname = await get_player_by_id_or_string(sock, team_id, author[1:])
                if not pid:
                    await channel.send("Could not find player by string '{}'.".format(author[1:]))
                    continue
                if await is_player_online(pid, team_id):
                    await sock.send(json.dumps( {
                       "@class": ".SendTeamChatMessageRequest",
                       "teamId": team_id,
                       "message": json.dumps({"type":"chat", "msg": reply}),
                       "requestId": "reply"
                    }))
                else:
                    new_queue.append(msg)
            else:
                await sock.send(json.dumps( {
                    "@class": ".SendTeamChatMessageRequest",
                    "teamId": team_id,
                    "message": json.dumps({"type":"chat", "msg": "{}\n{}".format(author, reply)}),
                    "requestId": "reply"
                }))

        state['queued_messages']=state.get('queued_messages',{})
        state['queued_messages'][str(channel.id)]=new_queue
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
            to_discord = "{}".format(postmsg)

            # use webhook for impersonation - test: do not delete after use
            webhooks = await channel.webhooks()
            temp_webhook = None
            hook_name = 'gb-'+str(channel.id)
            for hook in webhooks:
                if hook.name == hook_name:
                    temp_webhook = hook
                    break
            if not temp_webhook:
                temp_webhook = await channel.create_webhook(name = hook_name)
            colour = int(chat.get('colour', '0xffffff'), 16)
            embed = discord.Embed(colour=colour, description=to_discord)
            await temp_webhook.send(embed=embed, username=line['who'])
            #await temp_webhook.delete()

            if chatmsg['type']=='join':
                 time.sleep(0.1)
                 await channel.send("?playerinfo -id "+line['fromId'])

            last_posted_message[team_id] = line['when']
            state['last_posted_message'] = last_posted_message
            keep_state()

            if not chat.get('read_only'):
              for join in set(joined.keys()):
                logger.info("promoting or banning {}.".format(join))
                await welcome_and_promote(sock, team_id, join, joined[join])

        # get team activity
        request = json.dumps({
            "@class": ".LogEventRequest",
            "team_id": team_id,
           "eventKey": "GET_TEAM_REQUEST",
           "requestId": "gtr"
        })
        team_name=""
        await sock.send(request)

        online = []
        matches = {}
        while True:
            message = await sock.recv()
            data=json.loads(message)

            if data.get('requestId')=="gtr":
                team_data = data.get('scriptData',{})
                team_name = team_data.get('teamName','')
                members = team_data.get('members')
                for mem in members:
                    pid = mem.get('id')
                    name = mem.get('displayName')
                    is_online = mem.get('online')
                    if is_online:
                        if pid != chat.get('playerid'):
                            online.append(name)
                    match = mem.get('scriptData',{}).get('active_match')
                    if not match:
                        continue
                    matches[match]=(pid,name)
                    await watch(sock, match)
                break

        channel = await client.fetch_channel(settings['status-channel'])
        if not channel:
            logger.error("Could not retrieve status channel id")
            continue

        matchdata = ""
        num_matches = len(matches)

        while len(matches)>0:
            message = await sock.recv()
            data=json.loads(message)

            mid = data.get('requestId')
            if mid in matches:
                data = data.get('scriptData',{}).get('data')
                if not data:
                    logger.error("Could not spectate match. Aborting")
                    break
                peerid = bytes(data.get('serverip'), 'utf-8')
                port = data.get('serverport')
                auth_token = data.get('spectatortoken').encode()

                host = enet.Host(peerCount = 1)
                peer = host.connect(address=enet.Address(peerid, port), channelCount=2)

                #spectate a match match

                msg = bytes.fromhex('0122') + bytes([len(auth_token)]) + bytearray(auth_token)
                packet = enet.Packet(data=msg, flags=enet.PACKET_FLAG_RELIABLE)

                event = host.service(1000)
                #should be TYPE_CONNECT
                if event.type != enet.EVENT_TYPE_CONNECT:
                    logger.info("NOT CONNECTED")
                else:
                    logger.info("Spectating to get info.")

                    success = peer.send(0, packet)
                    ping = peer.ping()

                    while True:
                        event = host.service(10000)
                        if event.type == enet.EVENT_TYPE_DISCONNECT:
                            break
                        elif event.type == enet.EVENT_TYPE_RECEIVE:
                            packet_type = event.packet.data[0]
                            length = len(event.packet.data)

                            if packet_type == 0x10 and length>100:
                                #there are some 6 byte packets
                                data = event.packet.data
                                prev = 0
                                pnum = 0
                                players = {}

                                while pnum<4:
                                    start = data.find(b'{"player_data":', prev)
                                    if start<1:
                                        break
                                    stop = data.find(b'}}', start)
                                    stop = data.find(b'}}', stop+2)
                                    pbytes = data[start:stop+2]
                                    pjson = pbytes.decode()
                                    pdata = json.loads(pjson)

                                    pnum = pnum+1
                                    pid = pdata.get('player_data').get('account_id')
                                    if pid in players:
                                        break
                                    pname = pdata.get('player_data').get('display_name')
                                    players[pid]=pname
                                    plevel = pdata.get('player_data').get('level')
                                    ptrophies = pdata.get('player_data').get('trophies')

                                    matchdata += "> #{} - 🏆{} - {} (L{})\n".format(pnum, ptrophies, pname, plevel)
                                    prev=stop+1

                                peer.disconnect()
                                matchdata += "\n"
                                break

                            else:
                                # ignore any other packets. They are pings and OKs.
                                continue

            try:
                del matches[mid]
            except:
                pass
            if len(matches)==0:
                break

        # COMPLETE STATUS MESSAGE
        num_players = len(online)
        status_info = """__{}__ *updated: {} UTC*
{} Player{} online: {}.
Found {} ongoing match{}:
{}

""".format(team_name[4:], time.ctime(), num_players, (num_players!=1) and "s" or "", ", ".join(online),
           num_matches, (num_matches != 1) and "es" or "",
           matchdata)
        #########################

        edited = False
        status_message = state.get('status_message',{}).get(team_id)
        if status_message:
            try:
                message = await channel.fetch_message(status_message)
                if message:
                    await message.edit(content=status_info)
                    edited = True
                    time.sleep(5) # avoid some rate limiting
            except:
                # that's ok, we don't need to have it
                pass
        if not edited:
            message = await channel.send(status_info)
            messages = state.get('status_message',{})
            messages[team_id] = message.id
            state['status_message'] = messages
            keep_state()
            time.sleep(5) # avoid some rate limiting

        logger.info("Checking sales.")
        if not player_info:
            logger.info(" \ Account was online - skipped")
            # this happens when the account is already online.
            relogins += 1
            pass

        if player_info and team_data:
            if await can_sell_cards(sock, player_info):
                logger.info("Selling.")
                await sell_cards(sock, player_info, team_data)
            else:
                logger.info("Cannot sell yet.")

        await sock.send(json.dumps({ "@class": ".EndSessionRequest" }))
        logger.info("Finished checking "+chat['name'])
    logger.info("Finished background task.")

    is_running = False


async def can_sell_cards(sock, playerdata):
    # checking free slot
    if playerdata['serverTime'] > playerdata['scriptData']['freepack']['available_time']:
        logger.info('opening freepack')
        await sock.send(json.dumps({
            "@class": ".LogEventRequest",
            "eventKey": "OPEN_FREE_PACK",
            "requestId": 'free'
        }))
        # claim free stuff, link to star pack to reduce frequency
        await sock.send(json.dumps({
            "@class": ".LogEventRequest",
            "eventKey": "CLAIM_VIDEO_REWARD_PRIZE",
            "requestId": 'video'
        }))
    # checking star slot
    if playerdata['serverTime'] > playerdata['scriptData']['pinpack']['available_time'] and \
       int(playerdata['scriptData']['pinpack']['pin_count']) == 10:
        logger.info('opening starpack')
        await sock.send(json.dumps({
            "@class": ".LogEventRequest",
            "eventKey": "OPEN_PIN_PACK",
            "requestId": 'pin'
        }))

    for slot in range(1, 5):
        if playerdata['scriptData']['slot%d' % (slot)]['available_time'] == None and \
           playerdata['scriptData']['slot%d' % (slot)]['type'] == 11:
            logger.info('opening slot {}'.format(slot))
            await sock.send(json.dumps({"@class": ".LogEventRequest", "eventKey": "SLOT_OPEN_PACK", "SLOT_NUM": slot,
                                        "requestId": 'slot11'}))
        elif playerdata['serverTime'] > playerdata['scriptData']['slot%d' % (slot)]['available_time'] and int(
             playerdata['scriptData']['slot%d' % (slot)]['unlocking']) == 1:
            logger.info('opening slot {}'.format(slot))
            await sock.send(json.dumps({"@class": ".LogEventRequest", "eventKey": "SLOT_OPEN_PACK", "SLOT_NUM": slot,
                                        "requestId": 'slot'}))

    if playerdata['serverTime'] > playerdata['scriptData']['tokenTime']:
        return True
    return False

# TODO: clean up, this is copy and paste from the card seller bot
async def sell_cards(sock, playerdata, teamdata):
    teamcards = {}
    for hat in teamdata['teamCards']['hat']:
        teamcards['hats_' + hat] = teamdata['teamCards']['hat'][hat]['count']
    for golfer in teamdata['teamCards']['golfer']:
        teamcards['golfers_' + golfer] = teamdata['teamCards']['golfer'][golfer]['count']

    playerlevel = int(playerdata['scriptData']['data']['level'])
    sellabletypes = []
    for cardtype,tokenvalue in fullconfig['tokenvalues'].items():
        if tokenvalue <= fullconfig['salelevels'][str(playerlevel)]:
            sellabletypes.append(cardtype)
    foundcardtosell = False

    sortedteamcards = sorted(teamcards.items(), key=lambda x: x[1])
    for teamcard in sortedteamcards:
        teamcardtype = teamcard[0].split('_')[0]
        teamcardid = teamcard[0].split('_')[1]
        if teamcardid in playerdata['scriptData']['data'][teamcardtype]:
            cardtype = 'XXX'
            cardlimit = 6500
            if teamcardid in fullconfig[teamcardtype]:
                cardtype = fullconfig[teamcardtype][teamcardid]
            if cardtype in sellabletypes:
                if cardtype in fullconfig['cardlimits']:
                    cardlimit = fullconfig['cardlimits'][cardtype]
                    salecount = int(fullconfig['salelevels'][str(playerlevel)] / fullconfig['tokenvalues'][cardtype])
                    if (playerdata['scriptData']['data'][teamcardtype][teamcardid][
                            'level'] > 0 and
                        playerdata['scriptData']['data'][teamcardtype][teamcardid][
                            'count'] > salecount) or \
                            playerdata['scriptData']['data'][teamcardtype][teamcardid][
                                'count'] >= cardlimit + salecount:
                        saletype = teamcardtype[:-1]
                        saleitem = int(teamcardid)
                        foundcardtosell = True
                        break
    if foundcardtosell:
        logger.info("Selling: {} {} {}".format(saletype, saleitem, salecount))
        await sock.send(json.dumps({
          "@class": ".LogEventRequest",
            "eventKey": "TRADE_SELL_CARD",
            "CARD_TYPE": saletype,
            "CARD_ID": saleitem,
            "COUNT": int(salecount),
            "requestId": "sellcard"
        }))

client.run(settings.get('token'))
