# Comparison thread (pairs with docs/comparison.md)

1/ Three ways to get a sticker pack: draw it by hand, prompt a
generic image model, or run stickerloom. A quick honest thread.

2/ By hand (editor + @Stickers): total control, zero code. But every
sticker is manual, every Telegram rule is on you, and failures
surface at upload time. Best for one perfect sticker.

3/ Generic AI tools render fast — then hand you the sticker work:
backgrounds come back shaded, edges get touched, nothing is cut,
keyed, sized, or packed. You still do (and re-do) all of it.

4/ stickerloom automates the middle: chroma screen + margins baked
into the prompt, engine slices and keys each cell, 44 checks verify
every stage, gates approve the pack. The model is swappable
(Higgsfield / OpenAI / offline fake) — the pipeline is the product.

5/ It runs on your PC: plans, sheets, checks, reviews and packs all
land in `out/`, and `examples/fake_pack/` reproduces the path with
no account. Export/import moves packs as checksummed files.

6/ Bottom line: draw it if you want one perfect sticker. Run
stickerloom if you want a repeatable prompt-to-pack pipeline with
receipts. MIT, link in repo. (Long form: docs/comparison.md.)
