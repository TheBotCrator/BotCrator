"""
Bouncer
Written by aquova, 2018
https://github.com/aquova/bouncer
"""

import discord, json, sqlite3, datetime, asyncio, os, subprocess, sys
import Utils
from Member import User

# Reading values from config file
with open('config.json') as config_file:
    cfg = json.load(config_file)

# Configuring preferences
discordKey = str(cfg['discord'])
validInputChannels = cfg['channels']['listening']
logChannel = str(cfg['channels']['log'])
validRoles = cfg['roles']

sendBanDM = (cfg['DM']['ban'].upper() == "ON")
sendWarnDM = (cfg['DM']['warn'].upper() == "ON")

client = discord.Client()

# Create needed database, if doesn't exist
sqlconn = sqlite3.connect('sdv.db')
# 'num' is the number of warnings. A ban is counted noted as 0, notes are negative values
sqlconn.execute("CREATE TABLE IF NOT EXISTS badeggs (dbid INT PRIMARY KEY, id INT, username TEXT, num INT, date DATE, message TEXT, staff TEXT, post INT);")
sqlconn.execute("CREATE TABLE IF NOT EXISTS blocks (id TEXT);")
sqlconn.commit()
sqlconn.close()

warnThreshold = 3
lastCheck = datetime.datetime.fromtimestamp(1) # Create a new datetime object of ~0
checkCooldown = datetime.timedelta(minutes=5)

# Containers to store needed information in memory
recentBans = {}
blockList = []

helpInfo = {'$WARN':       '`$warn USER reason`',
            '$BAN':        '`$ban USER reason`',
            '$SEARCH':     '`$search USER`',
            '$NOTE':       '`$note USER message',
            '$REMOVE':     '`$remove USER',
            '$NOTEREMOVE': '`$noteremove USER`',
            '$BLOCK':      '`$block USER`',
            '$UNBLOCK':    '`$unblock USER',
            '$REPLY':      '`$reply USER`'}

# This is basically a makeshift enum
class LogTypes:
    NOTE = -1
    BAN = 0
    WARN = 1

# Searches the database for the specified user, given a message
# m: Discord message object
async def userSearch(m):
    try:
        user = User(m, recentBans)
    except User.MessageError:
        await client.send_message(m.channel, "I wasn't able to find a user anywhere based on that message. `$search USER`")
        return

    searchResults = user.search()
    try:
        username = user.getName(recentBans)
        if searchResults == []:
            await client.send_message(m.channel, "User {} was not found in the database\n".format(username))
            return
    except User.MessageError:
        await client.send_message(m.channel, "That user was not found in the database or the server\n")
        return

    out = "User {} was found with the following infractions\n".format(username)
    for item in searchResults:
        if item[1] == 0:
            out += "[{}] **{}** - Banned by {} - {}\n".format(Utils.formatTime(item[2]), item[0], item[4], item[3])
        elif item[1] < 0:
            out += "[{}] **{}** - Note by {} - {}\n".format(Utils.formatTime(item[2]), item[0], item[4], item[3])
        else:
            out += "[{}] **{}** - Warning #{} by {} - {}\n".format(Utils.formatTime(item[2]), item[0], item[1], item[4], item[3])

        if item[1] == warnThreshold:
            out += "They have received {} warnings, it is recommended that they be banned.\n".format(warnThreshold)
    await client.send_message(m.channel, out)

# Note a warn or ban for a user
# m: Discord message object
async def logUser(m, state):
    try:
        user = User(m, recentBans)
    except User.MessageError:
        if state == LogTypes.NOTE:
            await client.send_message(m.channel, "I wasn't able to understand that message: `$note USER`")
        else:
            await client.send_message(m.channel, "I wasn't able to understand that message: `$log USER`")
        return

    sqlconn = sqlite3.connect('sdv.db')
    if state == LogTypes.WARN:
        count = sqlconn.execute("SELECT COUNT(*) FROM badeggs WHERE id=? AND num > 0", [user.id]).fetchone()[0] + 1
    else:
        count = state
    globalcount = sqlconn.execute("SELECT COUNT(*) FROM badeggs").fetchone()[0]
    currentTime = datetime.datetime.utcnow()
    mes = Utils.removeCommand(m.content)
    if len(m.attachments) != 0:
        for item in m.attachments:
            mes += '\n{}'.format(item['url'])

    try:
        username = user.getName(recentBans)
    except User.MessageError:
        username = "ID: " + str(user.id)
        await client.send_message(m.channel, "I wasn't able to find a username for that user, but whatever, I'll do it anyway.")

    params = [globalcount + 1, user.id, username, count, currentTime, mes, m.author.name]

    # Generate message for log channel
    if state == LogTypes.BAN:
        logMessage = "[{}] **{}** - Banned by {} - {}\n".format(Utils.formatTime(currentTime), params[2],  m.author.name, mes)
    elif state == LogTypes.WARN:
        logMessage = "[{}] **{}** - Warning #{} by {} - {}\n".format(Utils.formatTime(currentTime), params[2], count, m.author.name, mes)
    else:
        logMessage = "Note made for {}".format(username)

    # Send ban recommendation, if needed
    if (count >= warnThreshold):
        logMessage += "This user has received {} warnings or more. It is recommened that they be banned.".format(warnThreshold)
    await client.send_message(m.channel, logMessage)

    logMesID = 0
    if state != LogTypes.NOTE:
        # Send message to log channel
        try:
            logMes = await client.send_message(client.get_channel(logChannel), logMessage)
            logMesID = logMes.id
        except discord.errors.InvalidArgument:
            await client.send_message(m.channel, "The logging channel has not been set up in `config.json`. In order to have a visual record, please specify a channel ID.")

        # Send a DM to the user
        try:
            u = user.getMember()
            if u != None:
                if state == LogTypes.BAN and sendBanDM:
                    await client.send_message(u, "You have been banned from the Stardew Valley server for violating one of our rules: {} If you have any questions, feel free to DM one of the staff members.".format(mes))
                elif state == LogTypes.WARN and sendWarnDM:
                    await client.send_message(u, "You have received Warning #{} in the Stardew Valley server for violating one of our rules: {} If you have any questions, feel free to DM one of the staff members.".format(count, mes))

        # I don't know if any of these are ever getting tripped
        except discord.errors.HTTPException as e:
            await client.send_message(m.channel, "ERROR: While attempting to DM, there was an unexpected error. Tell aquova this: {}".format(e))
        except discord.errors.Forbidden:
            await client.send_message(m.channel, "ERROR: I am not allowed to DM the user. It is likely that they are not accepting DM's from me.")
        except discord.errors.NotFound:
            await client.send_message(m.channel, "ERROR: I was unable to find the user to DM. I'm unsure how this can be the case, unless their account was deleted")

    # Update database
    params.append(logMesID)
    sqlconn.execute("INSERT INTO badeggs (dbid, id, username, num, date, message, staff, post) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", params)
    sqlconn.commit()
    sqlconn.close()

# Removes last database entry for specified user
# m: Discord message object
async def removeError(m, state):
    try:
        user = User(m, recentBans)
    except User.MessageError:
        if state == LogTypes.NOTE:
            await client.send_message(m.channel, "I wasn't able to understand that message: `$noteremove USER`")
        else:
            await client.send_message(m.channel, "I wasn't able to understand that message: `$remove USER`")
        return

    # Find most recent entry in database for specified user
    sqlconn = sqlite3.connect('sdv.db')
    # There's probably a clever way to reduce this to one line
    if state == LogTypes.NOTE:
        searchResults = sqlconn.execute("SELECT dbid, username, num, date, message, staff, post FROM badeggs WHERE id=? AND num < 0", [user.id]).fetchall()
    else:
        searchResults = sqlconn.execute("SELECT dbid, username, num, date, message, staff, post FROM badeggs WHERE id=? AND num > -1", [user.id]).fetchall()

    if searchResults == []:
        await client.send_message(m.channel, "That user was not found in the database.")
    else:
        item = searchResults[-1]
        sqlconn.execute("REPLACE INTO badeggs (dbid, id, username, num, date, message, staff, post) VALUES (?, NULL, NULL, NULL, NULL, NULL, NULL, NULL)", [item[0]])
        out = "The following log was deleted:\n"
        if item[2] == LogTypes.BAN:
            out += "[{}] **{}** - Banned by {} - {}\n".format(Utils.formatTime(item[3]), item[1], item[5], item[4])
        elif item[2] == LogTypes.NOTE:
            out += "[{}] **{}** - Note by {} - {}\n".format(Utils.formatTime(item[3]), item[1], item[5], item[4])
        else:
            out += "[{}] **{}** - Warning #{} by {} - {}\n".format(Utils.formatTime(item[3]), item[1], item[2], item[5], item[4])
        await client.send_message(m.channel, out)

        # Search logging channel for matching post, and remove it
        if item[6] != 0:
            async for m in client.logs_from(client.get_channel(logChannel)):
                if str(m.id) == str(item[6]):
                    await client.delete_message(m)
                    break
    sqlconn.commit()
    sqlconn.close()

# Prevents DM from a specific user from being forwarded
# message: Discord message object
# block: Boolean, true for block, false for unblock
async def blockUser(m, block):
    global blockList
    try:
        user = User(m, recentBans)
    except User.MessageError:
        await client.send_message(m.channel, "I wasn't able to understand that message: `$block USER`")
        return

    sqlconn = sqlite3.connect('sdv.db')
    if block:
        if user.id in blockList:
            await client.send_message(m.channel, "Um... That user was already blocked...")
        else:
            sqlconn.execute("INSERT INTO blocks (id) VALUES (?)", [user.id])
            blockList.append(user.id)
            await client.send_message(m.channel, "I have now blocked {}. Their messages will no longer display in chat, but they will be logged for later review.".format(user.id))
    else:
        if user.id not in blockList:
            await client.send_message(m.channel, "That user hasn't been blocked...")
        else:
            sqlconn.execute("DELETE FROM blocks WHERE id=?", [user.id])
            blockList.remove(user.id)
            await client.send_message(m.channel, "I have now unblocked {}. You will once again be able to hear their dumb bullshit in chat.".format(user.id))
    sqlconn.commit()
    sqlconn.close()

# Sends a private message to the specified user
async def reply(m):
    try:
        user = User(m, recentBans)
    except User.MessageError:
        await client.send_message(m.channel, "I wasn't able to understand that message: `$reply USER`")
        return

    u = user.getMember()
    if u == None:
        await client.send_message(m.channel, "Sorry, but they need to be in the server for me to message them")
        return
    try:
        await client.send_message(u, "A message from the SDV staff: {}".format(Utils.removeCommand(m.content)))
        await client.send_message(m.channel, "Message sent.")

    # I don't know if any of these are ever getting tripped
    except discord.errors.HTTPException as e:
        await client.send_message(m.channel, "ERROR: While attempting to DM, there was an unexpected error. Tell aquova this: {}".format(e))
    except discord.errors.Forbidden:
        await client.send_message(m.channel, "ERROR: I am not allowed to DM the user. It is likely that they are not accepting DM's from me.")
    except discord.errors.NotFound:
        await client.send_message(m.channel, "ERROR: I was unable to find the user to DM. I'm unsure how this can be the case, unless their account was deleted")

async def notebook(m):
    sqlconn = sqlite3.connect('sdv.db')
    allNotes = sqlconn.execute("SELECT * FROM badeggs WHERE num=-1").fetchall()
    sqlconn.commit()
    sqlconn.close()

    out = "You asked for it...\n"
    for item in allNotes:
        note = "[{}] **{}** - Note by {} - {}\n".format(Utils.formatTime(item[4]), item[2], item[6], item[5])
        if len(note) + len(out) < 2000:
            out += note
        else:
            await client.send_message(m.channel, out)
            out = note
    await client.send_message(m.channel, out)

# Special function made for SDV multiplayer beta release
# If matching phrase is posted, then post link to bug submission thread
async def checkForBugs(message):
    global lastCheck
    if ("BUG" in message.content.upper() and ("REPORT" in message.content.upper() or "FOUND" in message.content.upper())):
        if str(message.channel.id) in ["440552475913748491", "137345719668310016", "189945533861724160"]:
            await client.send_message(message.channel, "Did I hear someone say they found a MP bug? :bug:\nIf you wanna help out development of Stardew Valley, there's a link you can send your bug reports: <https://community.playstarbound.com/threads/stardew-valley-multiplayer-beta-known-issues-fixes-2.145034/>")
            lastCheck = datetime.datetime.utcnow()

@client.event
async def on_ready():
    global blockList
    print('Logged in as')
    print(client.user.name)
    print(client.user.id)

    sqlconn = sqlite3.connect('sdv.db')
    blockDB = sqlconn.execute("SELECT * FROM blocks").fetchall()
    blockList = [x[0] for x in blockDB]
    sqlconn.close()

    game_object = discord.Game(name="for your reports!", type=3)
    await client.change_presence(game=game_object)

@client.event
async def on_member_ban(member):
    global recentBans
    recentBans[member.id] = "{}#{}".format(member.name, member.discriminator)

@client.event
async def on_member_remove(member):
    # I know they aren't banned, but still we may want to log someone after they leave
    global recentBans
    recentBans[member.id] = "{}#{}".format(member.name, member.discriminator)

@client.event
async def on_message(message):
    global validInputChannels
    global logChannel
    if message.author.id == client.user.id:
        return
    try:
        # If they sent a private DM to bouncer
        if message.channel.is_private:
            if message.author.id in blockList:
                ts = message.timestamp.strftime('%Y-%m-%d %H:%M:%S')
                mes = "{} <{}> {}\n".format(ts, "{}#{}".format(message.author.name, message.author.discriminator), message.content)
                if message.attachments != []:
                    for item in message.attachments:
                        mes += ' ' + item['url']
                with open("DMs.txt", 'a', encoding='utf-8') as openFile:
                    openFile.write(mes)
            else:
                mes = "**{}#{}** (ID: {}): {}".format(message.author.name, message.author.discriminator, message.author.id, message.content)
                if message.attachments != []:
                    for item in message.attachments:
                        mes += '\n' + item['url']
                await client.send_message(client.get_channel(validInputChannels[0]), mes)

        # Temporarily notify if UB3R-BOT has removed something on its word censor
        elif (message.author.id == "85614143951892480" and message.channel.id == "233039273207529472") and ("Word Censor Triggered" in message.content):
            await client.send_message(client.get_channel(validInputChannels[0]), "Uh oh, looks like the censor might've been tripped.")

        # If a user pings bouncer
        elif client.user in message.mentions:
            mes = "**{}#{}** (ID: {}) pinged me in <#{}>: {}".format(message.author.name, message.author.discriminator, message.author.id, message.channel.id, message.content)
            if message.attachments != []:
                for item in message.attachments:
                    mes += '\n' + item['url']
            await client.send_message(client.get_channel(validInputChannels[0]), mes)

        # If they have privledges to access bouncer functions
        elif (message.channel.id in validInputChannels) and Utils.checkRoles(message.author, validRoles):
            if len(message.content.split(" ")) == 1:
                if message.content.upper() == "$HELP":
                    helpMes = "Issue a warning: `$warn USER message`\nLog a ban: `$ban USER reason`\nSearch for a user: `$search USER`\nCreate a note about a user: `$note USER message`\nShow all notes: `$notebook`\nRemove a user's last log: `$remove USER`\nRemove a user's last note: `$noteremove USER`\nStop a user from sending DMs to us: `$block/$unblock USERID`\nReply to a user in DMs: `$reply USERID`\nDMing users when they are banned is `{}`\nDMing users when they are warned is `{}`".format(sendBanDM, sendWarnDM)
                    await client.send_message(message.channel, helpMes)
                elif message.content.upper() == "$NOTEBOOK":
                    await notebook(message)
                elif message.content.upper() in helpInfo.keys():
                    await client.send_message(message.channel, helpInfo[message.content.upper()])
                elif message.content.upper() == "$UPDATE" and message.author.id == "254640676233412610":
                    await client.send_message(message.channel, "Updating and restarting...")
                    subprocess.call("git pull", shell=True)
                    sys.exit()
                return

            if message.content.startswith("$search"):
                await userSearch(message)
            elif message.content.startswith("$warn"):
                await logUser(message, LogTypes.WARN)
            elif message.content.startswith("$ban"):
                await logUser(message, LogTypes.BAN)
            elif message.content.startswith("$remove"):
                await removeError(message, LogTypes.BAN)
            elif message.content.startswith("$block"):
                await blockUser(message, True)
            elif message.content.startswith("$unblock"):
                await blockUser(message, False)
            elif message.content.startswith("$reply"):
                await reply(message)
            elif message.content.startswith("$noteremove"):
                await removeError(message, LogTypes.NOTE)
            elif message.content.startswith("$note"):
                await logUser(message, LogTypes.NOTE)

        # A five minute cooldown for responding to people who mention bug reports
        if (datetime.datetime.utcnow() - lastCheck > checkCooldown):
            await checkForBugs(message)
    except discord.errors.HTTPException:
        pass

client.run(discordKey)
