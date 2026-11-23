---
name: askmeclearly
description: How to show the user what they must judge or check — asking them to decide, and reporting progress or results they will check — through to learning the user's preferences from the answers. Read this before asking the user for any judgment — which change to accept or which alternative to choose, which direction or approach to confirm before building, or which screen, feature, or text option to choose — and before writing a progress or result report the user will read. Use it for one question or several, before a change or after it. Skip it for what research can settle and for what you have been trusted to decide yourself.
---

# askmeclearly

The reader is a person, and a person does not take in a message the way you, an AI, do. You can be handed every detail and use all of it. A person holds only a few things in mind at once, skims, comes to your message from other work knowing none of the names and steps you built up while working, and gives up when reading costs too much. So more detail does not make their judgment safer. It makes it harder: every fact you add draws attention from the few the judgment turns on. Do not hand over what you know or what you did. Give the conclusion and only what the user could not judge or check as well without, in words they already use, and leave the rest where it can be opened.

Make the short question and choice names carry the decision: the user can identify the subject, compare the consequences, and find the matching evidence without searching the surrounding explanation. Learn preferences from the answers and use them in the next question and the next piece of work.

Write user-facing output in the language the user is currently using.

## Before asking

- Confirm that the user's judgment is really needed. Fix an unmet requirement within your authority, or report it as unmet; do not ask the user to accept it as though it were an equivalent alternative.
- Read the user's preferences kept in this environment (memory, instruction files, and so on) and apply them to how you ask and to what the options contain.
- One question is one decision. Identify what the user is choosing now: an alternative, acceptance of a result, or permission to start a defined scope. Keep the alternatives on that same axis. Include the current state or deferral when it is a real choice; do not require a “Now + numbered options” set. Usually show at most three actionable alternatives; narrow a larger set yourself and name any materially different discarded choice and reason so the user can overturn it.

## Shape of one question

The short question and choice names may be shown separately from the explanation. Make that part sufficient to answer: name the user-visible subject and location or action, what is being decided now, and the concrete consequence of each choice. Use the user's words; do not make commits, files, or functions the subject. Put the meaning in the names themselves rather than leaving it to “Now”, “Option 1”, “this”, or a distant paragraph. Choose parallel names that answer the same question. Put distinguishing words first when space is limited. The working system owns numbering and reply syntax.

Under each choice, show only the evidence and differences needed to judge it. Compare the same subject, area, and conditions across alternatives. Place the matching image, recording, or text excerpt with that choice and refer to it by the same subject or action. Image marks identify areas or steps, not questions or choices; keep those identities distinct and readable without decoding bare numbers. If a choice changes several areas, each belongs to that same choice.

Keep decision-changing limitations and unknowns in the short comparison, where they can be read before selecting. State genuinely shared constraints once close to the choices. Say what selecting authorizes and what remains afterward; a proposal, a trial, an implementation approval, and completion are different commitments. Do not present a missing requirement as a preference or hide it by making the names simpler.

Finish with a brief recommendation and reason, and a way to answer supported by the working system. The names in the recommendation, evidence, and reply instructions must identify the same choices. Use that system's required headings and delivery location; in ordinary chat, stack the choices vertically with a short heading and consequence for each.

In ordinary chat, where no working system gives the question its form, the result reads like this. It shows the shape only, with its pictures left out; write your own names and lines, as plain Markdown rather than a code block.

```markdown
**Where should Export go on the invoice list?**

**Keep Export in the toolbar**
- Toolbar: Export beside Filter
- Exporting: one click
- Toolbar: five buttons

**Move Export into each row's menu**
- Row menu: Export under ⋯
- Exporting: two clicks
- Toolbar: four buttons, not five

Recommended: keep Export in the toolbar, because it is used daily and a second click would slow it.
Reply with the name of the choice.
```

Use pictures when they help the user judge the difference. For a visual comparison, use short “where: what you see” lines when they make the difference easier to scan. If using ◀, it marks a line that differs between the choices.

In the question itself, do not draw with characters: no layouts in code blocks, no box-drawing frames, no columns lined up with spaces, no choices side by side. A chat wraps lines at its own width, and many chat fonts do not give a full-width character exactly two columns, so such drawings fall apart. Keep each bullet short enough to fit one line on a phone (about 20 full-width characters).

Choose the picture by what changes:

- Screens: the image is the real screen cropped to the area around the change, with each change outlined in red and labelled with the area’s name, and the same area for every choice. Do not capture unrelated parts. The bullets name that area and what appears there, using the same area or step label as the image. Make the images as in "Making screen images" below. When the difference is in motion (a hover, an animation, a flow across screens), a still cannot show it: attach a short recording too, made as in "Marking screen recordings".
- Features, behavior, approaches: if this environment has a way to draw diagrams (a diagram tool, a screen that renders a diagram format, and so on) and it would be clearer, attach a diagram of the flow; keep it to about five boxes and mark what differs between choices. The bullets give the flow on one line with arrows (`A → B → C`).
- Long text (instructions, policies, explanations): do not lay out the full text. For each option, summarize what changes and lay out only the differences. Point to where the full text can be opened in one line.

Attach images in whatever form this environment can show in the chat; if it cannot, give the file location in one line.

## Shape of one report

A report is for what the user does next with the result: accept it, look at it, use it, or step in. A question is a report that ends in a decision: when you need one, report only what that decision rests on, then ask in the shape above. When to report and where a report is kept belong to the working system (a task tool, a chat); this section shapes only what the user reads.

1. Headline: one line, in the user's words, on what happened or what is now true. Do not make internal names (commits, files, functions, run IDs) the subject. It must stand alone: lists, notifications, and hover cards may show only this line.
2. What the user needs in order to act on the result, in short lines: what is different for them now, what does not hold or could not be checked, what is left for them to decide or do. Write numbers as digits.
3. Picture, when the result is something seen: the real screen cropped to the area the report is about, with that area outlined in red and labelled (see "Making screen images"; in a report, box what the report is about rather than the difference between the alternatives). Give every picture its purpose in one line, as its caption or alt text (`![Review card: answer buttons](path)`). For before and after, use the same crop for both.
4. Only when needed: one line on what comes next, or on what you need from the user.

Leave out how you got there (steps, logs, how many tests or review rounds ran, what you tidied up), what you noticed that does not change this result (if the user must act on it, report it on its own), and what the previous report already said. Say what a check means for the user (it works as asked, or this part is unverified), not how much checking there was. A record of the work that other instructions require goes where the working system keeps records, or into a linked file, not into what the user reads. Link to files for details instead of pasting them. "Still working" with nothing new is not a report.

## Making screen images

`scripts/annotate.mjs` crops every choice's screenshot to one shared area and draws the red boxes and labels on it, so the user's eye lands on the change. It works on any PNG, whatever captured it, and needs only Node 18.3 or later: nothing to install.

1. Capture each choice at full size with what this environment has: browser automation for a web page, the OS screenshot command for a desktop app (on macOS `screencapture -l <window id>`), the simulator's screenshot command for a phone app. For an option that is not built yet, apply its look to the real screen (in the page, a test build, a mock) and capture that; never draw a screen by hand. Record where each change is, in the screenshot's coordinates or in CSS pixels from the page (`getBoundingClientRect()`).
2. Write a spec next to the screenshots. Box only what differs between the compared choices; an unchanged reference can be left unmarked. A label identifies the same area or step as the bullets, in a few words (about 24 characters, or 12 wide CJK ones), for example `Review · green`.

   ```json
   {
     "scale": 2,
     "images": [
       { "input": "now.png", "output": "q1-now.png", "marks": [] },
       { "input": "opt1.png", "output": "q1-opt1.png",
         "marks": [{ "x": 40, "y": 120, "width": 160, "height": 28, "label": "Name" }] }
     ]
   }
   ```

   `scale` multiplies every coordinate (2 for CSS pixels on a 2x screenshot). Without `crop`, the shared area is every box and label of every image plus `pad` (default 24); give `crop` (`x`, `y`, `width`, `height`) to fix it yourself. A label sits above its box, or below it when there is no room. Labels may be in any language: the script picks, from the fonts installed on the computer, ones that have the label's characters. Set `lang` (a BCP 47 tag such as `zh-CN`, `ja`, `ko`) when the labels contain Chinese characters, so they take that region's forms.
3. Run `node <this skill's folder>/scripts/annotate.mjs spec.json`. It prints the output files, the area, and the fonts used. `--color` (default `#e0242a`), `--font-size` (default 13), and `--stroke` (default 3) change the look; keep the defaults unless the screen makes red or the size hard to see.
4. Open every output and check that the box sits on the change, the label does not hide it, and every choice shows the same area.

Windows and macOS have fonts for most scripts; on Linux, labels need fonts for their script (for example the Noto families), or characters show as boxes. Emoji in Apple's color format cannot be drawn, so leave emoji out of labels. When Node is not available, capture just the area around the change without marks, and let the bullets and `◀` carry the difference.

## Marking screen recordings

In a recording the user's eye has nowhere to land, and Playwright's video does not even show the mouse. Mark a recording the way screen images are marked: the pointer and a ring where it pressed, a red box labelled with the area’s name on what to look at, a caption saying what happens at each step, and a zoom when the thing is small. `scripts/video-marks.mjs` notes all of these while Playwright records, without touching the recording; `scripts/annotate-video.mjs` then draws them over it in the look of `annotate.mjs`. Because the recording stays clean, a wrong label or time is fixed in the spec and drawn again, not recorded again.

1. Record with Playwright, at the viewport's size so the text stays sharp. Keep one scene per recording, a few steps long.

   Make it for a person who has never seen this screen and watches it once, shrunk into a chat window about 800 px wide. For every step that person does three things, one after another: reads the caption to learn what to watch, follows the pointer with their eye to where it goes, and looks at what changed until they understand it. A script does a whole step in a fraction of a second, so each of the three gets lost: a caption that appears as the action starts is read after the action is over; a pointer that jumps, or sweeps in from a corner, reads as a cut, and the eye loses it; a result replaced before it is read was never seen; a box shown for a moment flashes instead of pointing. So give each its own time: let the caption be read before its action, move the pointer as a hand would, slowly enough to follow, and leave each result, box, and caption up until someone new has taken it in. Do not swing the other way: a screen that stands still with nothing new to read feels stuck.

   At that size the page's own text shrinks to about half and cannot be read, so zoom in on what each step asks the viewer to read: a zoom keeps the page's text at its own size when its area, widened or heightened to the video's shape, is no wider than about 800 px of the page. Box both what the pointer acts on and what changes because of it, so the viewer connects the two; when they are far apart, show both boxed on the whole screen first, then zoom in on the change. Let a click's result settle before zooming in on it, rather than moving the view while the page moves. Put the pointer near its first target before the first caption, at least 60 px inside the frame's edges (the drawn arrow is about 30 px wide); the drawing runs from just before the first caption or box to the end of the last one, so the page's loading and that first move are cut, and it ends on the last result. A whole recording script, run with Node where Playwright is installed (replace the folder, the address, and the steps):

   ```js
   import { chromium } from "playwright"; // or "@playwright/test"
   import { openMarkedPage } from "/path/to/askmeclearly/scripts/video-marks.mjs";

   const viewport = { width: 1440, height: 900 };
   const browser = await chromium.launch();
   const context = await browser.newContext({ viewport, recordVideo: { dir: "videos", size: viewport } });
   const { page, marks } = await openMarkedPage(context); // creates the page; its video starts here
   await page.goto("http://localhost:3000/");
   await page.locator(".row").first().waitFor();             // the page has settled
   const row = await page.locator(".row").first().boundingBox();
   const [x, y] = [row.x + row.width / 2, row.y + row.height / 2];
   const inside = (v, size) => Math.min(Math.max(v, 60), size - 60);
   await page.mouse.move(inside(x + 100, viewport.width), inside(y, viewport.height)); // near the row, inside the frame

   marks.caption("① Hover a row: its card opens");
   await page.waitForTimeout(1500);
   await page.mouse.move(x, y, { steps: 40 });
   await page.waitForTimeout(500);
   const card = await page.locator(".card").boundingBox();
   marks.box(row, "Row", { seconds: 5 });     // without seconds: until marks.clear()
   marks.box(card, "Its card", { seconds: 5 });
   await page.waitForTimeout(2000);
   marks.zoom(card, { seconds: 3 });            // the card's text, readable at 800 px
   await page.waitForTimeout(3000);

   marks.caption("② Click it to open the task");
   await page.waitForTimeout(1500);
   await page.mouse.click(x, y);
   await page.waitForTimeout(800);              // the page has changed
   const task = await page.locator(".task-title").boundingBox();
   marks.box(task, "The task", { seconds: 3 });
   marks.zoom(task, { seconds: 3 });
   await page.waitForTimeout(3000);
   marks.clear();

   const video = page.video();
   await context.close();
   await video.saveAs("raw.webm");
   await marks.save("spec.json", { input: "raw.webm", output: "marked.mp4" });
   await browser.close();
   ```

   The pointer and presses are noted by themselves, so move and click with the real mouse (`page.mouse`); `locator.click()` jumps the pointer straight onto its target. Number captions when they identify ordered steps; label boxes with the area’s name, using the same words as the explanation. Keep labels as short as in screen images.
2. Run `node <this skill's folder>/scripts/annotate-video.mjs spec.json`. It needs Node 18.3 or later and a Chromium-family browser already on the computer: it looks for Chrome, Edge, Chromium, and Brave where they usually install, and `--browser <program>` names any other. Nothing to install. It plays the recording once in real time, so it takes as long as the recording, and keeps only the part from half a second before the first caption, box, or zoom to when the last one ends. `--color`, `--font-size`, and `--stroke` are those of `annotate.mjs`, sized for the video watched 800 px wide, or at its own size when narrower. The browser draws the text, so captions and labels may be in any script, right to left too.
3. To fix a mark, edit `spec.json` (a label, `from`/`to` in seconds, a box, a zoom, or `start`/`end`, the part kept) and run it again. Keep `output` apart from `input`: the clean recording is what every new drawing starts from.
4. The output's extension picks the format: `.mp4` (H.264) plays almost everywhere, chat apps and phones included; `.webm` (VP9) plays in browsers. Use the one the place showing it can play.
5. Watch the output once as that newcomer would, at normal speed and about 800 px wide: each caption is read before its action, each result stays until it is taken in, the text that matters can be read, each box sits on its thing while it is on screen, and the pointer is where the mouse was. Where a browser can open the file, play it there; otherwise look at frames every half second.

When the screen is not a page Playwright can drive, or no Chromium-family browser is installed, send the recording without these marks and let the bullets say where to look and at which second.

## Before sending

- Read only the short question and choice names first: can a reader identify the subject and location or action, the decision, and each consequence without searching elsewhere? Then check that the nearby evidence has the same identity and that constraints affecting the choice remain visible.
- For each part, ask whether the user could judge or check as well without it; if so, cut it, even when it is true and took work to find. Decide it on the message itself, not by whether you or another AI found it clear, and ask again after each fix.
- Compare each picture and image against the difference you wrote: does it really show it, is the red outline or `◀` in the right place. Read the body once without the images: the choices must still be told apart.
- After summarizing long text, compare against the full text sentence by sentence: no difference between options is missing, and anything you called "the same behavior" really is.
- When you cannot show a difference (no capture, no material), do not fill the gap with an invented picture; say so.
- Use a means that publishes outside only when the user has allowed it.
- With several questions, follow any rule in the user's preferences (such as one question at a time). Otherwise number them so the answers can come back in one line.

## After the reply

- Read the answer per number. Treat an unanswered question as pending; do not decide it yourself.
- Record what was decided and proceed as the working system and the user's instructions direct.

## Learning preferences from the answers

Keep only preferences that will matter for later decisions and work. Which option was chosen belongs in the work record.

1. Line up the chosen options, the rejected options, and the reasons, and look for a shared idea (for example, rejects options that make the screen taller, picks the shorter wording).
2. Make a new preference only from two or more answers in the same direction, or when the user says it applies beyond this question ("always", "from now on"). A single answer, however sound its reason, only supports an existing preference or goes into the work record as a "candidate".
3. Find how this environment keeps the user's preferences (a memory folder or tool, instruction files, and so on) and save it under that system's rules, including whether confirmation is needed before writing. If the same preference, or one that extends the same idea to another context, already exists, revise that one instead of adding a new one. If you are about to cite the same answer as evidence for both an existing and a new preference, they are one preference — merge them. If you find no such system, put what you learned in the reply and let the user decide where to keep it.
4. Each saved preference states the preference, the evidence (when, which question, which answer — pointing to a place that can be opened later), and how to use it next. Do not rewrite working rules (who decides what) because of a preference. If they should change, propose it in the reply.

Tell the user in one or two lines what you learned and where you saved it.
