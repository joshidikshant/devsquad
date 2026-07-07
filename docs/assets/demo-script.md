# DevSquad demo recording script

The single highest-leverage add for stars: a ~20-second loop of the hook
firing mid-session, placed right under the README hook headline. Record once,
drop the file in `docs/assets/`, and add the embed snippet (below) to the README.

## Option A — asciinema (recommended: crisp, tiny, copy-pastable text)

```bash
brew install asciinema agg          # agg converts .cast → .gif
asciinema rec docs/assets/demo.cast --cols 90 --rows 24 --idle-time-limit 2
# …perform the beats below, then Ctrl-D to stop…
agg docs/assets/demo.cast docs/assets/demo.gif --theme monokai --font-size 20
```

Then in README.md, directly under the headline block:

```markdown
<div align="center">
  <img src="docs/assets/demo.gif" alt="DevSquad hook firing mid-session" width="720">
</div>
```

## The beats (perform these during the recording)

Keep it fast — this is a loop, not a tutorial. ~20 seconds total.

1. **Set the scene (2s).** A clean prompt in a real project:
   ```
   $ claude
   ```
2. **The ask (3s).** Type a request that will trigger bulk reading:
   ```
   > review this codebase and find the auth flow
   ```
3. **The reads scroll (4s).** Let Claude read a few files — the viewer sees
   `Read src/…` lines tick by. This builds the "it's about to burn context"
   tension.
4. **THE MONEY SHOT (5s).** The hook fires and injects its suggestion — pause
   here so it's readable:
   ```
   ⚡ DevSquad: You've read 20 files this session. Delegate bulk reading
      to @gemini-reader (1M context) to preserve your context window.
      Estimated savings: ~40K tokens this session.
   ```
5. **The payoff (4s).** Show the delegation landing — `@gemini-reader`
   returns a summary, and Claude continues with its context intact.
6. **The tag (2s).** End on `/devsquad:status` showing the delegation logged:
   ```
   Gemini: 1 invocation · Grok: 0 · Direct: 3 self-calls
   ```

## Recording tips

- Use a wide-but-short terminal (90×24) so the GIF is landscape and readable
  inline. Bump the font size (`--font-size 20`).
- Trim dead air with `--idle-time-limit 2` (already in the rec command).
- If a live session is fiddly to script, you can stage it: run the beats in a
  demo project with `holdout_mode=false` so the suggestion always fires, and
  read 20 real files first so the threshold trips on cue.
- Keep the total under ~250KB so it loads instantly on the README. `agg`'s
  default output is small; if it's heavy, drop to `--font-size 18`.

## Option B — screen capture (if you want the real Claude Code TUI chrome)

Record the Claude Code terminal with QuickTime / Kap, then convert:
```bash
brew install gifski
gifski --width 720 --fps 12 -o docs/assets/demo.gif recording.mov
```
Same beats. Slightly larger file, but shows the authentic UI.

---

Whichever you pick, the placement matters more than the polish: **directly
under the hook headline, above the fold.** A browser who *sees* the hook fire
converts far better than one who reads that it does.
