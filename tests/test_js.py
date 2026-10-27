"""The JavaScript that has no DOM in it (agent.js helpers: the HTML escaping of the chat) under node's built-in test runner. node is a test dependency: without it this FAILS
(an escaping suite that silently skips is no suite). `STICKERLOOM_SKIP_JS=1` skips it on purpose."""
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from functools import lru_cache
from stickerloom.console.assets import ASSETS, UI_FILES, asset_path


@lru_cache(maxsize=1)
def console_sources():
    import json
    helper = Path(__file__).parent / 'js' / 'console_source.js'
    return json.loads(subprocess.check_output(['node', str(helper)], text=True, encoding='utf-8'))


def read_console(path):
    if path.suffix == '.js':
        return console_sources()[path.name]
    return (asset_path(path.name) if path.name in ASSETS else path).read_text(encoding='utf-8')


class JavaScriptTests(unittest.TestCase):
    def test_node_suite(self):
        node = shutil.which("node")
        if os.environ.get("STICKERLOOM_SKIP_JS") == "1":
            self.skipTest("STICKERLOOM_SKIP_JS=1")
        if not node:
            self.fail("node is not installed: the escaping tests of the chat need it (set STICKERLOOM_SKIP_JS=1 to skip them on purpose)")
        root = Path(__file__).resolve().parent.parent
        files = sorted(str(f.relative_to(root)) for f in (root / "tests" / "js").glob("*.test.js"))
        r = subprocess.run([node, "--test", *files], cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)   # node writes UTF-8; the Windows codepage cannot read a curly quote
        self.assertEqual(r.returncode, 0, r.stdout[-1500:] + r.stderr[-800:])


class StudioActionTests(unittest.TestCase):
    """The Studio's buttons are `data-act=name`, handled by `ACT.name = ...`. The scripts share ONE `ACT` object, so a second `ACT.name =` in a later script silently
    replaces the first: `history.js` (the retired History screen) did that to `hopen`, so every batch in "Earlier batches" opened batch NaN. A name may be defined twice
    only on purpose, and every deliberate replacement is listed here."""

    INTENTIONAL = {"ggo": "composer.js"}        # the Generate menu replaces the old input row's Generate handler (it keeps `_ggo`)

    @staticmethod
    def scripts():
        import re
        ui = Path(__file__).resolve().parent.parent / "stickerloom" / "console"
        html = read_console(ui / "index.html")
        order = re.findall(r"/ui/([A-Za-z0-9_.-]+\.js)", html)                          # the order the page loads them in
        return ui, order

    def test_an_action_is_not_silently_replaced_by_a_later_script(self):
        import re
        ui, order = self.scripts()
        self.assertTrue(order)
        defs: dict[str, list[str]] = {}
        for name in order:
            src = (asset_path(name) if name in ASSETS else ui / name).read_text(encoding='utf-8')
            for m in re.finditer(r"\bACT\.([A-Za-z0-9_]+)\s*=(?!=)", src):
                defs.setdefault(m.group(1), []).append(name)
        twice = {act: files for act, files in defs.items() if len(files) > 1}
        unexpected = {act: files for act, files in twice.items() if self.INTENTIONAL.get(act) != files[-1] or len(files) != 2}
        self.assertEqual(unexpected, {}, "an action assigned twice: the later script silently replaces the earlier handler (rename one, or list it in INTENTIONAL)")
        self.assertEqual(set(self.INTENTIONAL) - set(twice), set(), "INTENTIONAL lists an action that is no longer replaced: delete the entry")

    def test_every_button_of_the_studio_has_a_handler(self):
        import re
        ui, order = self.scripts()
        defined, used = set(), {}
        for name in order + ["index.html"]:
            src = (asset_path(name) if name in ASSETS else ui / name).read_text(encoding='utf-8')
            defined |= set(re.findall(r"\bACT\.([A-Za-z0-9_]+)\s*=(?!=)", src))
            for m in re.finditer(r"data-act=\"?([A-Za-z0-9_]+)", src):
                used.setdefault(m.group(1), name)
        self.assertEqual({k: v for k, v in used.items() if k not in defined}, {}, "a button whose action nothing handles")

    @staticmethod
    def block(src: str, marker: str) -> str:
        """Extract the production statement by its parsed AST, independent of multiline formatting."""
        helper = Path(__file__).parent / 'js' / 'console_source.js'
        code = "const s=require(process.argv[1]);let data='';process.stdin.on('data',c=>data+=c);process.stdin.on('end',()=>{const [src,marker]=JSON.parse(data);process.stdout.write(s.statement(src,marker))});"
        import json
        return subprocess.check_output(['node', '-e', code, str(helper)], input=json.dumps([src,marker]), text=True)

    def test_the_earlier_batches_title_is_plain_text_and_the_list_is_not_paged_by_a_button(self):
        """The list has a plain title and no "Load more": the list scrolls and asks for its next page by itself.
        Nothing folds the list away, nothing remembers that in localStorage."""
        ui, order = self.scripts()
        src = read_console(ui / "live.js")
        head = self.block(src, "function histColHTML()")
        self.assertNotIn("htoggle", src, "the collapsible Earlier-batches header is gone")
        self.assertNotIn("stickerloom.hopen", src, "the fold-away choice (localStorage stickerloom.hopen) is gone")
        self.assertNotIn("let HO=", src, "the fold state is gone")
        self.assertIn("<h1>Projects</h1>", head, "the title is plain text above the rows (the list of every project, D2: it was Earlier batches)")
        self.assertIn("HB.items.map(histEntry)", head, "every project is an entry (a batch alone is a row, histRow)")
        for n in order + ["index.html"]:
            self.assertNotIn("hmore", read_console(ui / n), f"no 'Load more' button action left in {n}")
        self.assertIn("histLoad(true)", self.block(src, "function histCol()"), "the column asks for its next page when it is scrolled near the end")
        css = read_console(ui / "studio.css")
        import re
        self.assertIsNone(re.search(r"\.lv-hh(?!ead)", css), "the styles of the old header button are gone")
        self.assertNotIn("#ghist.open", css)
        self.assertNotIn(".lv-hmore", css, "the Load more button's style is gone")

    def test_a_click_on_a_batch_presents_that_batch_and_only_that_batch(self):
        """: showing several batches at once in the Studio is not wanted. A click on a batch in the column makes it THE batch the Studio presents (ACT.hopen, the
        same action as the credits pill's recent batches): one session, one batch, one Studio view. The stacked open cards, their open-set, their localStorage key and the per-card
        copy of the Studio's header, views, bar and element ids are gone."""
        ui, order = self.scripts()
        src = read_console(ui / "live.js")
        row = self.block(src, "const histRow=(it,k,inPack)=>{")
        self.assertIn("data-act=hopen", row, "the row presents the batch")
        hopen = self.block(src, "ACT.hopen=el=>{")
        self.assertIn("gens:[it.id]", hopen, "the session becomes exactly this one batch")
        self.assertIn("location.hash='#/studio'", hopen, "from any other screen it goes to the Studio")
        everything = "".join(read_console(ui / n) for n in order + ["studio.css"])
        for gone in ("HX.open", "hxSave", "stickerloom.hbopen", "histItem", "studioFor", "cardTab", "ACT.hbx", "data-act=hbx", "gcardview", "lv-hcard", "lv-hitem", "scopeGens", "const CT=", "pfx"):
            self.assertNotIn(gone, everything, "the stacked cards are gone completely: " + gone)
        for needs in (".lv-hrow", ".lv-hrow.on"):
            self.assertIn(needs, read_console(ui / "studio.css"), "the presented batch is marked in the column")

    def test_the_history_block_is_gone_and_the_batch_has_a_particles_section(self):
        """: the "History of G###" block under the open batch (every decision on its stickers, the AI captions) was useless and is removed completely: markup, state, actions,
        styles. The decisions stay in result.json and in GET /api/generations/<id>/history. What sits under the presented batch now is its Particles section (particles.js, drawn into #gpart):
        one list of the particle versions made for its stickers (each once), and the approval offer for stickers not in a pack."""
        import re
        ui, order = self.scripts()
        live = read_console(ui / "live.js")
        css = read_console(ui / "studio.css")
        everything = "".join(read_console(ui / n) for n in order)
        for gone in ("History of ", "AI captions", "const HX=", "hxLoad", "hxSync", "hxBatch", "hxSticker", "hcapRun", "ACT.hcap", "ACT.hsx", "ACT.hgx", "ACT.hgall", "ACT.hretry", "ACT.vlmoff", "ACT.vlmno", "ghist", "lv-hdet"):
            self.assertNotIn(gone, live.replace("typeof hxSync", ""), "the history block is gone: " + gone)
        for gone in ("#ghist", ".lv-hhead", ".lv-ht{", ".lv-hsh", ".lv-hsm", ".lv-hgh", ".lv-hgs", ".lv-hcap", ".lv-hcell", ".lv-hline", ".lv-hbar", ".lv-hcb", ".lv-hdet", ".lv-hempty"):
            self.assertNotIn(gone, css, "its styles are gone: " + gone)
        self.assertIsNone(re.search(r"data-act=(hcap|hsx|hgx|hgall|hretry|vlmoff|vlmno)\b", everything), "no button of the old block is left")
        draw = self.block(live, "function drawHist(){")
        self.assertIn("histCol()", draw, "the Earlier-batches column is still drawn here")
        self.assertIn("spSecDraw()", draw, "under the batch: its Particles section")
        self.assertIn("'gpart'", self.block(live, "function ensureBars(){"), "the section's container replaces #ghist")
        self.assertIn("#gpart", css)
        for keep in ("const vlmState=", "const vlmSet=", "ACT.vlmyes=", "const VLM="):
            self.assertIn(keep, live, "the consent for AI vision is still asked once and remembered: " + keep)
        sp = read_console(ui / "particles.js")
        for needs in ("function spSecBatchHtml(", "Approve as a pack", "/api/generations/${b.gid}/particles", "ptRow(", "spLinkIndex(", "data-act=spopen"):
            self.assertIn(needs, sp)
        for gone in ("Shared by ", "Uses the shared particles above", "made for each sticker of this batch"):
            self.assertNotIn(gone, sp, "the batch's particles are one list of versions, not a block per sticker: " + gone)

    def test_the_particles_tab_is_a_studio_step_in_its_own_file(self):
        """: a new Studio tab 'Particles' next to Stickers and Animation, in its own file (particles.js, names prefixed SP / sp). It is registered in index.html (after effects.js, whose
        functions it shares) and in the server's UI_FILES; generate.js owns the Studio's header and body, so the tab is wrapped in, the way live.js wraps drawRail."""
        import re
        ui, order = self.scripts()
        html = read_console(ui / "index.html")
        self.assertIn("particles.js", order, "index.html loads it")
        self.assertLess(order.index("effects.js"), order.index("particles.js"), "it uses effects.js's functions: loaded after it")
        self.assertEqual(UI_FILES['particles.js'], 'text/javascript', 'the server serves it')
        sp = read_console(ui / "particles.js")
        mine = re.findall(r"^(?:const|let|function|class)\s+([A-Za-z_$][\w$]*)", sp, re.M)
        self.assertTrue(mine)
        self.assertEqual([n for n in mine if not n.startswith(("SP", "sp"))], [], "every top-level name of particles.js starts with SP / sp (the scripts share one global scope)")
        others = {}
        for n in order:
            if n != "particles.js":
                for name in re.findall(r"^(?:const|let|function|class)\s+([A-Za-z_$][\w$]*)", read_console(ui / n), re.M):
                    others.setdefault(name, n)
        self.assertEqual({n: others[n] for n in mine if n in others}, {}, "a top-level name declared by two scripts kills the second one")
        self.assertIn("GS.tab==='particles'", sp)
        self.assertIn("GSTUDIO.set('particles'", sp)
        self.assertNotIn("gbodyHtml=function", sp)
        self.assertNotIn("stepsHtml=function", sp)
        core = read_console(ui / "studio-core.js")
        self.assertIn("gExtraSteps(S,c)", core, "registered steps use Studio's renderer and numbering")
        for route in ("/api/effects", "data-fxx=sp"):
            self.assertIn(route, sp)
        css = read_console(ui / "studio.css")
        self.assertRegex(css, r"\.gsteps\{[^}]*display:flex", "the journey is one flex track: its stops (Particles and Telegram included) share it, however many there are")
        self.assertIn(".sp-modes", css)

    def test_the_effect_functions_serve_both_screens_and_say_reactions(self):
        """effects.js holds ONE implementation of an effect for the pro screen (state FX) and the Studio tab (state SP): every handler finds its state from its root (fxX, data-fxx). The drawn
        reactions are suggest -> chips -> price -> draw -> tick the cells -> particles_pick; a warning is a sentence and never a block; the size of the reactions travels as sprite_px / scale;
        what the person reads says 'reactions' (the chrome has no 'pieces')."""
        import re
        ui, _ = self.scripts()
        fx = read_console(ui / "effects.js")
        for route in ("/suggest", "/particles_estimate", "/particles`", "/particles_pick"):
            self.assertIn(route, fx)
        self.assertIn("sprite_px", read_console(ui / "particles.js"), "the size of the reactions travels as sprite_px / scale, with the burst maker's preview and render")
        self.assertIn("key_is_seamless:'The green screen has panels or patterns, so the keying may eat parts of the reactions. Use it anyway, or make another take.'", fx)
        self.assertIn("Draw ${fxCols(d.grid)} reactions with AI", fx)
        self.assertIn("Use it anyway", fx)
        self.assertNotIn("document.getElementById('fxpv", fx, "ids carry the state's name: two screens, two sets of previews")
        code = re.sub(r"/\*.*?\*/", "", fx, flags=re.S)
        code = re.sub(r"fx-pieces|\w*[Pp]iece\w*", "", code)
        self.assertIsNone(re.search(r"\bpieces?\b", code, re.I), "the chrome says reactions, not pieces")
        for name in ("const fxNew=", "const fxX=", "FXS.fx=", "function fxDrawn("):
            self.assertIn(name, fx)

    def test_every_list_bearing_screen_has_the_second_column(self):
        """Studio and Create list the earlier batches in the shared column (it used to exist only for Library, Pack, Chat and AI); Settings and the full-screen tools have none."""
        ui, _ = self.scripts()
        app = read_console(ui / "app.js")
        import re
        col2 = re.search(r"const COL2=\[(.*?)\]", app).group(1)
        for route in ("agent", "generate", "library", "pack", "chat", "create"):
            self.assertIn(f"'{route}'", col2)
        for route in ("settings", "editor", "export", "prepare", "animate"):
            self.assertNotIn(f"'{route}'", col2)
        self.assertIn("histCol()", self.block(app, "function drawCol2()"))

    def test_the_credits_pill_reads_the_history_grid(self):
        """The drop-down under the credits pill lists the same batches as the history cards: it draws its one thumbnail from `cells`, not from the four-thumbnail shortcut."""
        ui, _ = self.scripts()
        src = read_console(ui / "composer.js")
        self.assertNotIn("it.thumbs", src, "`thumbs` is gone from GET /api/history: the pill would throw on every open")
        self.assertIn("it.cells", src)

    def test_the_green_screen_panel_stays_when_a_batch_is_a_video(self):
        """The Animation tab shows the Video sheet AND the green screen (its cut lines, chips and the animation box): the second one used to be replaced."""
        import re
        ui, _ = self.scripts()
        src = read_console(ui / "generate.js")
        # since 2026-10-04 both are views of ONE sheet panel (its tabs: the sheet views, To send, Video): neither replaces the other
        self.assertIn("sheetPanel(g,mode==='anim'?'anim':'still')", self.block(src, "function batchHtml("), "the batch shows the sheet panel in both tabs")
        panel = self.block(src, "function sheetPanel(")
        self.assertIn("if(view==='video')body=videoPanel(g)", panel, "the Video view is the video panel")
        self.assertIn("cutSvg(g,true,true,2)", panel, "the green-screen view keeps its cut lines and boxes")
        self.assertIn("tab('video','Video')", panel)


class ShellTests(unittest.TestCase):
    """ONE shell for every screen. The rail is declared in app.js (six items, AI first), the column widths are tokens, and the narrow-screen layout is the
    shell's, not one screen's: it used to exist only under `body.agent-view` in agent.css, so only AI had a phone layout."""

    @staticmethod
    def ui():
        return Path(__file__).resolve().parent.parent / "stickerloom" / "console"

    def test_the_rail_declares_four_items_and_the_avatar_holds_the_rest(self):
        import re
        ui = self.ui()
        app = read_console(ui / "app.js")
        rail = re.search(r"const RAIL=\[(.*?)\],RAILOF", app).group(1)
        items = re.findall(r"\['(\w+)','(\w+)','([^']+)'\]", rail)
        self.assertEqual([i[2] for i in items], ["Home", "Create", "Library", "Help"])     # the rail of four
        for to in ("settings", "users", "history"):                                           # the avatar menu keeps every screen the old rail reached
            self.assertIn(f"data-act=nav data-to={to}", app)
        self.assertIn("data-act=rtrash", app); self.assertIn("data-act=aulogout", app)
        for tab in ("agent", "generate", "create", "effects"):                              # Create's tabs and its + New menu
            self.assertIn(f"'{tab}'", re.search(r"const CREATE_TABS=\[(.*?)\]", app).group(1))
        screens = re.search(r"const SCREENS=\[(.*?)\]", app).group(1)
        html = read_console(ui / "index.html")
        for route, icon, _ in items:
            self.assertIn(f"'{route}'", screens, f"the rail's {route} is a screen")
            self.assertIn(f"id=s-{route}", html, f"the rail's {route} has a section")
            self.assertRegex(app, rf"\b{icon}:'<", f"the rail's icon {icon} exists")
        self.assertIn("id=ctabs", html)
        self.assertNotIn("RAIL.unshift", read_console(ui / "agent.js"), "a later script must not register itself in the navigation")

    def test_the_narrow_layout_is_the_shells_and_not_one_screens(self):
        import re
        ui = self.ui()
        studio = read_console(ui / "studio.css")
        agent = read_console(ui / "agent.css")
        self.assertNotIn("agent-view", agent + studio + read_console(ui / "app.js"), "no screen has its own shell")
        for token in ("--rail-w:", "--col2-w:"):
            self.assertIn(token, studio)
        self.assertNotRegex(agent, r"grid-template-columns:\s*(96px|var\(--rail-w\))", "agent.css does not size the page grid")
        self.assertNotRegex(agent, r"#rail|#app|#col2\{", "agent.css does not restyle the shell")
        phone = studio[studio.index("@media (max-width:760px){\n body #app"):]
        self.assertIn("#rail{order:2;flex-direction:row", phone, "every screen gets the bottom bar on phones")
        self.assertIn("body.col2 #col2{left:0", phone)
        self.assertIn("#c2tog", studio)
        self.assertIn("id=c2tog", read_console(ui / "index.html"))

    def test_every_screen_shares_the_glow_the_page_width_and_the_row_states_of_ai(self):
        """Library takes AI's glow and AI's hover / selection; the plain screens sit in ONE page width with ONE header; a heading's icon is sized (the Settings
        Telegram icon used to fill the page because nothing sized it)."""
        import re
        ui = self.ui()
        css = read_console(ui / "studio.css")
        self.assertIn("--glow-bg:", css)
        self.assertRegex(css, r"#app\{[^}]*background:var\(--glow-bg\)", "the glow is behind the whole shell, columns included")
        self.assertRegex(css, r"\.page\{max-width:\d+px;margin:0 auto\}")
        self.assertRegex(css, r"h1 svg,h2 svg\{width:\d+px")
        for name in ("app.js", "editor.js", "packs.js"):
            self.assertNotRegex(read_console(ui / name), r'<div style="max-width:\d+px;margin:0 auto">', f"{name}: a screen's width is the shared .page, not its own number")
        self.assertIn("class=page", read_console(ui / "app.js"))
        agent = read_console(ui / "agent.css")
        for state in ("hover{background:#F0F9FC}", "on{background:#E3F4F9}"):          # AI's chat rows: the pack rows, the chat rows and the batch rows read the same
            self.assertIn(state, agent.replace(" ", ""))
            self.assertIn(state, css.replace(" ", ""))
        self.assertIn("drawCol2()", self.block_of(read_console(ui / "packs.js"), "RENDER.pack="), "a pack opened by its address draws its column after the library is read")

    @staticmethod
    def block_of(src, marker):
        return src[src.index(marker):].split("\n", 1)[0]

    def test_the_composer_has_no_palette_of_its_own_and_the_style_tiles_are_compact(self):
        """docs/design.md, Studio: the composer panel was a near-black navy island in a light app. It takes the shared glass surface, and the style tiles are small wrapping
        squares chosen by a ring and a check (they were six huge portrait cards in a horizontal scroller). The tiles are read from the API, never listed in the page."""
        import re
        ui = self.ui()
        css = read_console(ui / "studio.css")
        for navy in ("#070b1c", "#050816", "#2a52ff", "#0b1124", "#0e1633", "#3466ff", "#eaf0ff"):
            self.assertNotIn(navy, css, "the composer's navy island is gone: " + navy)
        self.assertRegex(css, r"\.cp\{[^}]*background:var\(--glass\)")
        styles = re.search(r"\.cp-styles\{([^}]*)\}", css).group(1)
        self.assertIn("flex-wrap:wrap", styles)
        tile = re.search(r"\.cp-style\{([^}]*)\}", css).group(1)
        self.assertNotIn("aspect-ratio:3/4", tile)
        self.assertRegex(tile, r"width:\d{2,3}px")
        self.assertIn(".cp-style.on", css)
        self.assertIn("LIVE.m.styles.map(", read_console(ui / "composer.js"), "the tiles come from the API's presets")
        comp = read_console(ui / "composer.js")
        self.assertIn("data-act=cpstyles", comp, "one Style chip in the bar opens the tiles")
        self.assertIn("if(!CP.styles){el.innerHTML='';return}", comp, "the tiles are hidden until the chip is clicked")
        self.assertIn(".cp-styles:empty{display:none}", css)

    def test_sizes_and_radii_come_from_one_scale_and_everything_answers_focus(self):
        """six type steps and one radius scale, so a screen cannot drift; every interactive thing has a keyboard focus ring; motion respects the user's setting."""
        import re
        ui = self.ui()
        css = {n: read_console(ui / n) for n in ("studio.css", "agent.css")}
        for name, text in css.items():
            bare = [m for m in re.findall(r"font-size:\s*([0-9.]+)px", text)]
            self.assertEqual(bare, [], f"{name}: a font size outside the scale (use var(--fs-xs|s|m|l|xl|2xl)): {bare[:8]}")
            radii = [m for m in re.findall(r"border-radius:\s*([^;}]+)", text) if re.search(r"(?<![\w-])(?:[5-9]|[1-9]\d)px", m)]
            self.assertEqual(radii, [], f"{name}: a radius outside the scale (use var(--r-s|r-m|r|r-l|r-pill)): {radii[:8]}")
        self.assertRegex(css["studio.css"], r"--fs-xs:[\d.]+px;--fs-s:[\d.]+px;--fs-m:[\d.]+px;--fs-l:[\d.]+px;--fs-xl:[\d.]+px;--fs-2xl:[\d.]+px")
        self.assertIn("button:focus-visible", css["studio.css"])
        self.assertIn("prefers-reduced-motion:reduce){*,", css["studio.css"].replace(" ", ""))

    def test_the_ai_screen_shows_the_styles_and_the_settings_under_its_box(self):
        """: the AI screen shows the styles too, smaller. They are the Studio's presets (from GET /api/chat/agent), picked with one click; picking one before the first
        message must not create an empty chat (it waits in A.pre and is applied when the chat starts), and the grid and spending chips are the gear's settings, one click away."""
        import re
        ui = self.ui()
        js = read_console(ui / "agent.js")
        css = read_console(ui / "agent.css")
        self.assertIn("id=ag-styles", js)
        self.assertIn("(A.agent&&A.agent.styles)", js, "the tiles come from the API's presets, never a list in the page")
        pick = self.block_of(js, "ACT.agstyle=")
        self.assertIn("saveSet({style_id:A.pre})", pick, "with a chat, the pick is the chat's setting")
        self.assertIn("else drawBar()", pick, "without a chat, nothing is created")
        self.assertIn("applyPre()", StudioActionTests.block(js, "async function ensureSession("), "the pick is applied when the chat is created")
        self.assertIn('data-act="aggridtoggle"', js)
        self.assertIn("data-act=agsetask", js)
        for sel in (".ag-styles", ".ag-st", ".ag-st.on", ".ag-chip"):
            self.assertIn(sel, css)
        self.assertNotIn("#ai-sheet", js)

    def test_the_particle_effects_screen_is_wired_into_create_and_calls_the_api(self):
        """docs/architecture.md ("Reactions"): Create has a Reaction effects tool that opens #/effects; the entry is the emoji bank (no auto-playing stickers) and reads GET /api/particles/emoji-motions; the screen reads and writes only /api/effects and, for the burst, /api/particles (nothing is decided in the browser), a paid video needs
        `go`, and the fixed Queue pill must not be able to cover its last buttons."""
        import re
        ui = self.ui()
        fx = read_console(ui / "effects.js")
        html = read_console(ui / "index.html")
        self.assertIn("id=s-effects", html)
        self.assertIn("/ui/effects.js", html)
        self.assertIn('effects.js', UI_FILES)
        app = read_console(ui / "app.js")
        self.assertIn("'effects'", re.search(r"const SCREENS=\[(.*?)\]", app).group(1))
        self.assertIn("effects:'agent'", app, "the rail keeps Create lit on the effects screen")
        self.assertNotIn("data-act=fxopen", read_console(ui / "editor.js"), "Create no longer offers Reaction effects: reactions start in the Studio")
        for route in ("/api/effects", "/estimate", "/video", "/add", "/plan"):
            self.assertIn(route, fx)
        # docs/architecture.md ("Reactions"): the burst (Motion and Finish) is the SET's, one preview for the pack: effects.js no longer previews or renders per sticker, particles.js calls the set's routes
        pt = read_console(ui / "particles.js")
        for route in ("/api/particles/${id}/preview", "/api/particles/${s.id}/render", "/api/particles/${id}/add"):
            self.assertIn(route, pt)
        for gone in ("/preview`", "/render`", "sticker_id:sid"):
            self.assertNotIn(gone, fx, "effects.js has no per-sticker preview or render any more")
        # docs/architecture.md ("Reactions"): Create > + New > Reaction effects is its own screen (#/reactions, reactions.js), the emoji bank only; #/effects stays the Studio's particle editor
        rx = read_console(ui / "reactions.js")
        self.assertIn("id=s-reactions", html)
        self.assertIn("/ui/reactions.js", html)
        self.assertIn('reactions.js', UI_FILES)
        self.assertIn("'reactions'", re.search(r"const SCREENS=\[(.*?)\]", app).group(1))
        self.assertIn("reactions:'agent'", app, "the rail keeps Create lit on Reaction effects")
        self.assertIn("data-to=reactions", app, "+ New > Reaction effects opens #/reactions, not the Studio's #/effects")
        for act in ("data-act=rxpick ", "data-act=rxcreate", "data-act=rxsave", "data-act=rxdl", "data-act=rxclose"):
            self.assertIn(act, rx)
        for group in ("rxGroup('Motion'", "rxGroup('Size'", "data-rxadvanced"):
            self.assertIn(group, rx)
        tabs = read_console(ui / "lottiestyle.js")
        for tab in ("emoji", "lottie", "packs"):
            self.assertIn(f"data-act=rxstyle data-s={tab}", tabs)
        for label in ("Emoji style", "Lottie style", "Packs"):
            self.assertIn(label, tabs)
        for route in ("/api/particles/emoji-motions", "/api/particles/emoji-preview", "/api/particles/emoji-lottie"):
            self.assertIn(route, rx + read_console(ui / "particles.js"), "the bank, preview and shared Packs list use the server")
        for gone in ("data-act=fxek", "/api/particles/emoji-lottie"):
            self.assertNotIn(gone, fx, "the emoji screen left effects.js")
        for gone in ("data-act=fxpack", "data-act=fxgo", "data-act=fxst", "coverMedia", "fx-cv"):
            self.assertNotIn(gone, fx, "no pack picker and no auto-playing stickers on the entry")
        self.assertIn("go:true", fx.replace(" ", ""), "the click on 'Make the video' is the go-ahead, and it shows the price")
        self.assertIn(".page.fx{padding-bottom", read_console(ui / "studio.css"))


if __name__ == "__main__":
    unittest.main()
