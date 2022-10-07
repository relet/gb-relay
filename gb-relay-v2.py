#!/usr/bin/env python3

import asyncio
import base64
from contextlib import suppress
from sqlite3 import connect
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
#import websockets
import requests
import time
import sys
import os

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

checker_email = settings.get('checker-email')
checker_password = settings.get('checker-password')

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

# welcome or ban people
welcomed={}
async def welcome_and_promote(team_id, who, pid):
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

async def warn_and_demote(team_id, who, pid, complaint):
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

async def boot_and_block(team_id, pid):
    redlist = state.get('redlist',[])
    if not pid in redlist:
        redlist.append(str(pid))
    state['redlist']=redlist
    keep_state()
    bot.boot_player(team_id, pid)

async def get_player_by_id_or_string(team_id, search):
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
        await connect_as(checker_email, checker_password)
        if not ignore_online and bot.is_player_online(chat['playerid'], team_id):
            continue

        channel = await client.fetch_channel(chat['channel'])
        if not channel:
            logger.error("Could not retrieve channel id "+chat['channel'])
            continue

        # login main account
        player_info = await connect_as(chat['email'], chat['pass'])
        logger.info("Logged in as "+player_info["playerId"])

        if not player_info['sessionId']:
            logger.error("Could not log in as "+chat['name'])
            continue

        authenticated = 'playerId' in player_info
        if not authenticated:
            logger.error("Could not authenticate.")
            continue

        logger.info("CHECK")

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
                pid, pname = await get_player_by_id_or_string(team_id, reply)
                if not pid:
                    await channel.send("Could not find player by string '{}'.".format(reply))
                    continue
                bot.boot_player(team_id, pid)
            elif author[0] == "!":
                pid, pname = await get_player_by_id_or_string(team_id, author[1:])
                if not pid:
                    await channel.send("Could not find player by string '{}'.".format(author[1:]))
                    continue
                if await is_player_online(pid, team_id):
                    bot.send_chat_message(team_id, reply)
                else:
                    new_queue.append(msg)
            else:
                bot.send_chat_message(team_id, "{}\n{}".format(author, reply))

        state['queued_messages']=state.get('queued_messages',{})
        state['queued_messages'][str(channel.id)]=new_queue
        keep_state()

        messages = bot.get_team_chat(team_id)

        to_handle = []
        last_posted_message=state.get('last_posted_message', {})
        newest = last_posted_message.get(team_id,0)

        # todo, use a filter instead
        for message in messages:
            if message['date'] <= newest:
                continue
            to_handle.append(message)
        to_handle.sort(key=lambda x: x['date'], reverse=False)

        joined={}
        for message in to_handle:
            chatmsg = message.get('content',{}).get('message')
            postmsg = chatmsg.get('msg')
            when = message.get('date')
            author = message.get("from",{}).get("name")
            authorId = message.get("from",{}).get("id")

            if chatmsg['type']=='leave':
                postmsg = chatmsg['msg']+' left the team.'
                if chatmsg['msg'] in joined:
                   del joined[chatmsg['msg']]
            if chatmsg['type']=='promote':
                postmsg = chatmsg['promoter']+' has promoted '+chatmsg['promoted']+"."
                if chatmsg['promoted'] in joined:
                    del joined[chatmsg['promoted']]
            if chatmsg['type']=='demote':
                postmsg = chatmsg['demoter']+' has demoted '+chatmsg['demoted']+"."
            if chatmsg['type']=='boot':
                postmsg = author+' has booted '+chatmsg['booted']+"."
            if chatmsg['type']=='join':
                postmsg = author+' joined the team. (player id: '+message.get("from",{}).get("id")+')'
                joined[author]=authorId
            if chatmsg['type']=='friendly_match':
                continue
                #postmsg = author+' started a friendly match.'

            to_discord = "{}".format(postmsg)

            # use webhook for impersonation
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
            await temp_webhook.send(embed=embed, username=author)

            if chatmsg['type']=='join':
                 time.sleep(0.1)
                 await channel.send("?playerinfo -id "+authorId)

            last_posted_message[team_id] = when
            state['last_posted_message'] = last_posted_message
            keep_state()

            if not chat.get('read_only'):
              for join in set(joined.keys()):
                logger.info("promoting or banning {}.".format(join))
                await welcome_and_promote(team_id, join, joined[join])

        logger.info("Finished checking "+chat['name'])
    logger.info("Finished background task.")

    is_running = False


client.run(settings.get('token'))
