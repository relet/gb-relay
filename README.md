# GB relay bot

A discord bot that acts as relay between Discord and the in-game team chat for 
Golf Blitz teams. All team messages are forwarded to a dedicated channel in
discord, and discord slash commands allow to reply to the in-game chat. 

It also welcomes new players to the team.

The following commands are provided
* /reply message - replies to one team channel
* /announce message - replies to all registered teams that are not set to 
  read_only
* /yellowcard player - issues a warning message to a player (by matching the 
  search string to the name or id - make sure it is unique)
* /redcard player - boots a player from the team and puts it on the red list.
* /boot player - Just boots a player from the team. 

Players on the red list will be booted within 60 seconds of joining the team.

# Requirements

Python3
```
discord                  1.7.3               
discord-py-interactions  4.0.2               
discord-py-slash-command 3.0.3               
```

# Setup 

tbd.

# Settings

tbd.
```
{
    "entry_url": "...",
    "hmac_key": "...",
    "admins": ["..."],
    "guild_ids": [integer],
    "token": "...your token here...",
    "checker-email":"account email",
    "checker-pass":"account password",
    "chats": [
        {
            "name": ...,
            "playerid": ..,
            "teamid": ...,
            "email": ...,
            "pass": ...,
            "channel": ...,
            "read_only": True|False
        },
        ...
    ]
}
```