import { LuExternalLink } from "react-icons/lu";

interface Props {
  src: string;
  url?: string;
}

/** Full-page screenshot of the page the clone was made from. */
function OriginalScreenshot({ src, url }: Props) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-border px-3 py-1.5 text-[11px] text-muted-foreground">
        <span className="shrink-0 font-medium uppercase tracking-wide">Original</span>
        {url && (
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex min-w-0 items-center gap-1 truncate font-mono hover:text-foreground"
            title="Open the live page"
          >
            <span className="truncate">{url}</span>
            <LuExternalLink className="h-3 w-3 shrink-0" aria-hidden="true" />
          </a>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-auto bg-muted/30">
        <img
          src={src}
          alt={url ? `Screenshot of ${url}` : "Screenshot of the original page"}
          className="block w-full"
          loading="lazy"
        />
      </div>
    </div>
  );
}

export default OriginalScreenshot;
