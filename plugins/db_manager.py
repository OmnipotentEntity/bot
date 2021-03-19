import asyncio
import jq
import psycopg2
import plugins.commands
import plugins.privileges
import plugins.reactions
import util.discord
import discord_client
import util.db
import util.db.kv
import util.asyncio

@plugins.commands.command("config")
@plugins.privileges.priv("shell")
async def config_command(msg, args):
    arg = args.next_arg()
    if arg == None:
        return await msg.channel.send(", ".join(
            util.discord.format("{!i}", nsp)
            for nsp in util.db.kv.get_namespaces()))

    if not isinstance(arg, plugins.commands.StringArg): return

    if arg.text == "--delete":
        nsp = args.next_arg()
        key = args.next_arg()
        if not isinstance(nsp, plugins.commands.StringArg): return
        if not isinstance(key, plugins.commands.StringArg): return

        util.db.kv.Config(nsp.text)[key.text] = None
        return await msg.channel.send("\u2705")

    nsp = arg
    key = args.next_arg()
    if key == None:
        return await msg.channel.send(", ".join(
            util.discord.format("{!i}", key)
            for key in util.db.kv.Config(nsp.text)))

    if not isinstance(key, plugins.commands.StringArg): return

    script = args.next_arg()
    if script == None:
        result = util.db.kv.Config(nsp.text)._config.get(key.text)
        if result == None:
            return await msg.channel.send("None")
        else:
            return await msg.channel.send(util.discord.format("{!i}", result))

    conf = util.db.kv.Config(nsp.text)
    input = conf._config.get(key.text, "null")
    conf[key.text] = jq.compile(script.text).input(text=input).first()
    return await msg.channel.send("\u2705")

@plugins.commands.command("sql")
@plugins.privileges.priv("shell")
async def config_command(msg, args):
    data_outputs = []
    outputs = []
    with util.db.connection() as conn:
        with conn.cursor() as cur:
            for arg in args:
                if (isinstance(arg, plugins.commands.CodeBlockArg)
                    or isinstance(arg, plugins.commands.InlineCodeArg)):
                    try:
                        await util.asyncio.concurrently(cur.execute, arg.text)
                    except psycopg2.Error as e:
                        outputs.append(util.discord.format("{!b}", e.pgerror))
                    else:
                        outputs.append(cur.statusmessage)
                        try:
                            results = await util.asyncio.concurrently(
                                cur.fetchmany, 1000)
                        except psycopg2.ProgrammingError:
                            pass
                        else:
                            data = [" ".join(
                                desc[0] for desc in cur.description)]
                            data.extend(" ".join(repr(col) for col in result)
                                for result in results)
                            if len(results) == 1000:
                                data.append("...")
                            data_outputs.append(data)
                            outputs.append(data)

            def output_len(output):
                return sum(len(row) + 1 for row in output)

            total_len = sum(4 + output_len(output) + 4
                if isinstance(output, list) else len(output) + 1
                for output in outputs)

            while total_len > 2000 and any(data_outputs):
                lst = max(data_outputs, key=output_len)
                if lst[-1] == "...":
                    removed = lst.pop(-2)
                else:
                    removed = lst.pop()
                    lst.append("...")
                    total_len += 4
                total_len -= len(removed) + 1

            text = "\n".join(util.discord.format("{!b}", "\n".join(output))
                if isinstance(output, list) else output
                for output in outputs)[:2000]

            reply = await msg.channel.send(text)

            # If we've been assigned a transaction ID, means we've changed
            # something. Prompt the user to commit.
            try:
                @util.asyncio.concurrently
                def txid():
                    cur.execute("SELECT txid_current_if_assigned()")
                    return cur.fetchone()[0]
                txid = await txid
            except psycopg2.Error:
                return
            if txid == None:
                return
            await reply.add_reaction("\u21A9")
            await reply.add_reaction("\u2705")
            with plugins.reactions.ReactionMonitor(
                guild_id=msg.guild.id, channel_id=msg.channel.id,
                message_id=reply.id, author_id=msg.author.id,
                event="add",
                filter=lambda _, p: p.emoji.name in ["\u21A9", "\u2705"],
                timeout_each=60) as mon:

                rollback = True
                try:
                    _, p = await mon
                    if p.emoji.name == "\u2705":
                        rollback = False
                except asyncio.TimeoutError:
                    pass

                @util.asyncio.concurrently
                def finish():
                    if rollback:
                        conn.rollback()
                    else:
                        conn.commit()
                await finish
                await reply.remove_reaction(
                    "\u2705" if rollback else "\u21A9",
                    member=discord_client.client.user)
