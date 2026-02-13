# Onboarding Preference Questions

## Question 1: Enforcement Mode

Present the enforcement mode options:

> **Enforcement Mode**
>
> This controls how aggressively DevSquad enforces delegation rules.
>
> - `advisory` (default): DevSquad suggests delegation but does not block. Claude can override suggestions when it judges direct handling is more efficient.
> - `strict`: DevSquad blocks non-compliant tool use and provides the delegation command to run instead. Claude must follow delegation rules.
>
> Current: [current value or "advisory (default)"]
>
> Which mode? (advisory/strict)

Accept the developer's response. If they provide no answer or an ambiguous response, default to `advisory`.

## Question 2: Default Routes

Present the default routing table:

> **Default Delegation Routes**
>
> These determine which agent handles each type of task:
>
> | Task Type | Default Route |
> |-----------|--------------|
> | Research / web search | gemini |
> | Large file reading | gemini |
> | Code generation / boilerplate | codex |
> | Testing | codex |
> | Synthesis / integration | claude (self) |
>
> Accept these defaults? Or would you like to customize routes?

If the developer accepts defaults, proceed. If they want customization, ask for each route individually:

> For each task type, specify: gemini, codex, or claude.
> - Research / web search: [current: gemini]
> - Large file reading: [current: gemini]
> - Code generation / boilerplate: [current: codex]
> - Testing: [current: codex]
> - Synthesis / integration: [current: claude] (recommended: always claude)

Only ask for customization if the developer explicitly requests it.

## Question 3: Agent Preferences

> **Agent Preferences**
>
> Fine-tune how DevSquad interacts with external agents:
>
> - `gemini_word_limit`: Maximum words to request in Gemini responses (current: 300)
> - `codex_line_limit`: Maximum lines for Codex outputs (current: 50)
> - `auto_suggest`: Automatically suggest delegation when detecting eligible tasks (current: true)
>
> Accept these defaults or customize?

If customizing, accept numeric values for limits and true/false for auto_suggest. Validate inputs:
- gemini_word_limit: must be a positive integer between 50 and 2000
- codex_line_limit: must be a positive integer between 10 and 500
- auto_suggest: must be true or false
