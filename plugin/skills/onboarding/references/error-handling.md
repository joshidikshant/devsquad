# Onboarding Error Handling

## jq Not Available

- Warn: "jq is not installed. Using basic JSON handling. For best results, install jq."
- Use jq-optional fallback functions in `lib/state.sh`
- Config still created correctly; complex JSON merging may be less robust

## No External CLI Available

- Complete onboarding normally (do not abort)
- Set all routes to "claude" as fallback
- Warn that delegation will not function until at least one external agent is installed
- Save config so it is ready when agents are installed later

## Detection Script Failure

- Fall back to basic `command -v` checks for gemini, codex, and claude
- Log detection method in config under `environment.detection_method`
- Continue with basic detection results

## General Recovery

- Never crash or abort mid-flow. Always complete with a status message.
- Write failure: report error and suggest checking file permissions
- Read failure on expected file: treat as fresh install scenario
- All error messages include actionable next steps
