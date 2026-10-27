# stickerloom vs the alternatives

Four honest ways to get a sticker pack, and where each wins.

## The manual path (a photo editor + @Stickers)

You draw or cut every sticker by hand, resize to 512px, and upload
through Telegram's @Stickers bot, which creates packs and reports usage
stats [1][2]. Total control, zero code, and the art is exactly yours.
The cost is time: every sticker is manual, every rule (512px side,
512KB, emoji assignment, pack plumbing) is on you, and there is no
second pair of eyes — a broken upload fails at the bot.

stickerloom keeps the @Stickers endpoint but automates the middle: the
engine cuts, keys, resizes and verifies, and 44 checks fail the pack
locally before anything leaves your PC.

**@Stickers wins when:** you want one perfect hand-drawn sticker, full
manual control, or zero new tools to learn.

## sticker-convert (move packs between messengers)

sticker-convert is the open-source converter for existing packs: it
moves sticker sets between Telegram, Signal, WhatsApp, Line, Discord
and more, with pre-built releases for Windows, macOS and Linux [3]. If
your art already exists as a pack somewhere, it is the fastest way to
get it onto Telegram in the right formats.

What it doesn't do is make art: there is no generation, no verification
against Telegram's limits, and no approval step. stickerloom sits
upstream of it — prompt to a verified pack — and its `export` output
(plain PNG/WebM files plus a manifest) is exactly the kind of input a
converter takes.

**sticker-convert wins when:** the stickers already exist and you just
need them converted, resized, or moved to another messenger.

## Generic AI image tools

A general image model renders a sheet fast, but it knows nothing about
stickers: backgrounds come back shaded, faces touch the edges, sizes
are wrong, and nothing is cut, keyed, named, or packed. You still do
the sticker work by hand — and you re-do it for every regeneration.

stickerloom is the sticker work: the sheet prompt bakes in the chroma
screen and the margins, the engine slices and keys each cell, the gates
approve or stop every stage, and export writes Telegram-ready PNG/WebM
plus a manifest. The model is a swappable provider (Higgsfield, OpenAI,
or the offline fake renderer) — the pipeline is the product.

**Generic AI wins when:** you need a single image, not a pack, and no
Telegram rules apply.

## In-app makers and sticker apps

Telegram's own sticker editor builds packs from your photos right
inside the chat app — open the sticker panel, tap (+), cut, draw, and
share [1]. Phone apps and bots are the same idea: convenient for
one-offs, zero install, instant sharing.

The tradeoff is the black box: no verification you can read, no local
files, no export format you own, and your prompts and faces go to
someone's server. stickerloom runs on your PC: every run writes its
plan, sheet, checks, gate reviews and pack files under `out/`, the
sample pack in `examples/fake_pack/` reproduces the whole path offline,
and the `export`/`import` round trip moves packs between machines as
plain files with a checksummed manifest.

**In-app makers win when:** the sticker is a quick reaction to share in
the next minute, not a pack to keep.

## Bottom line

If you want one perfect hand-drawn sticker, draw it. If your pack
already exists elsewhere, convert it. If you want a repeatable
prompt-to-pack pipeline with receipts at every stage — 44 checks,
human-or-auto gates, offline mode — that is stickerloom.

## Sources

[1] Telegram Stickers — sticker editor and the @Stickers manager:
https://core.telegram.org/stickers
[2] Importing Stickers to Telegram — the @stickers bot flow:
https://core.telegram.org/import-stickers
[3] sticker-convert — open-source cross-messenger sticker converter:
https://github.com/laggykiller/sticker-convert
