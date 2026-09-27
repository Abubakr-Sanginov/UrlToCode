export function OnboardingNote() {
  return (
    <div className="rounded-lg border border-brand-border bg-brand-subtle p-3 text-sm leading-6 text-foreground">
      <span>
        To use Screenshot to Code,{" "}
        <a
          className="inline text-brand underline underline-offset-2 hover:opacity-70"
          href="https://buy.stripe.com/8wM6sre70gBW1nqaEE"
          target="_blank"
          rel="noopener noreferrer"
        >
          buy some credits (100 generations for $36)
        </a>{" "}
        or use your own OpenAI API key with GPT4 vision access.{" "}
        <a
          href="https://github.com/abi/screenshot-to-code/blob/main/Troubleshooting.md"
          className="inline text-brand underline underline-offset-2 hover:opacity-70"
          target="_blank"
          rel="noopener noreferrer"
        >
          Follow these instructions to get yourself a key.
        </a>{" "}
        and paste it in the Settings dialog (gear icon above). Your key is only
        stored in your browser. Never stored on our servers.
      </span>
    </div>
  );
}
