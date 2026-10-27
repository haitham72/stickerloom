# 5 launch posts

1. stickerloom: one prompt in, a verified Telegram sticker pack out.
   44 checks, approval gates, and an offline mode that needs no
   account. MIT, on GitHub now.

2. The sticker part of AI stickers is not the render — it is the key,
   the cut, the 512px rule, the emoji, the pack plumbing. stickerloom
   automates all of it, with receipts at every stage.

3. Try it without signing up for anything:
   `STICKERLOOM_PROVIDER=fake stickerloom create "a happy cactus" --auto`
   That command builds a real verified 4-sticker pack on your PC.

4. Every stickerloom run writes its plan, sheet, checks, gate reviews
   and pack files to `out/`. Nothing is a black box; the sample pack
   in `examples/fake_pack/` reproduces the whole path offline.

5. Providers are swappable: Higgsfield and OpenAI for real art, a
   deterministic fake renderer for tests, demos, and CI. The pipeline
   is the product — the model is a setting.
