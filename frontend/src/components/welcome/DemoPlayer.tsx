import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { LuPlay, LuPause, LuVolume2, LuVolumeX } from "react-icons/lu";

/**
 * The demo, with its own controls.
 *
 * A native player would show the video and nothing else, so the explanation
 * of what is happening had to live somewhere else on the page and go stale
 * the moment the video was scrubbed. Here the chapters are beside the video
 * and follow it: the one being shown is marked, and each says what to look
 * for in the frame rather than only what the tool does.
 *
 * No `controls` attribute. The native ones cannot be restyled to match
 * anything and cannot be moved, and a player that looks like the one on
 * every other site is the one thing this page should not look like.
 */

export interface DemoChapter {
  /** Where in the video it starts, in seconds. */
  at: number;
  title: string;
  /** What the viewer should be looking at, which is not the same thing as
   *  what the feature is called. */
  text: string;
}

const DEMO_SRC = "/demo/urltocode-demo.mp4";
const DEMO_POSTER = "/demo/urltocode-demo-poster.jpg";

function clock(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

export function DemoPlayer({ chapters }: { chapters: readonly DemoChapter[] }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const trackRef = useRef<HTMLDivElement>(null);
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(false);
  const [current, setCurrent] = useState(0);
  const [duration, setDuration] = useState(0);
  const [scrubbing, setScrubbing] = useState(false);

  // The chapter being shown, taken as the last one that has already started.
  // Comparing against the next chapter's start would be exacter, and would
  // flicker for a frame on every boundary.
  const activeIndex = useMemo(() => {
    let index = 0;
    chapters.forEach((chapter, i) => {
      if (current >= chapter.at) index = i;
    });
    return index;
  }, [chapters, current]);

  const seekTo = useCallback((seconds: number) => {
    const video = videoRef.current;
    if (!video) return;
    video.currentTime = Math.max(0, Math.min(seconds, video.duration || seconds));
  }, []);

  function toggle() {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) {
      void video.play().catch(() => {
        // Autoplay is refused often enough that it cannot be relied on; the
        // button is there to press again.
        setPlaying(false);
      });
    } else {
      video.pause();
    }
  }

  function positionFrom(event: ReactPointerEvent<HTMLDivElement>): number {
    const track = trackRef.current;
    if (!track || !duration) return 0;
    const box = track.getBoundingClientRect();
    const ratio = (event.clientX - box.left) / Math.max(box.width, 1);
    return Math.max(0, Math.min(1, ratio)) * duration;
  }

  function onScrub(event: ReactPointerEvent<HTMLDivElement>) {
    seekTo(positionFrom(event));
    setCurrent(positionFrom(event));
  }

  // While the pointer is down the video keeps playing under the finger, so
  // what is being scrubbed to moves out from under it. Held until release.
  function onScrubMove(event: ReactPointerEvent<HTMLDivElement>) {
    if (!scrubbing) return;
    const at = positionFrom(event);
    seekTo(at);
    setCurrent(at);
  }

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const video = videoRef.current;
      if (!video) return;
      if (event.key === "ArrowRight") {
        event.preventDefault();
        seekTo(video.currentTime + 5);
      } else if (event.key === "ArrowLeft") {
        event.preventDefault();
        seekTo(video.currentTime - 5);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [seekTo]);

  const played = duration > 0 ? current / duration : 0;

  return (
    <div className="grid gap-8 lg:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)] lg:gap-10">
      <div>
        <div className="overflow-hidden rounded-xl border border-border bg-card shadow-raised">
          <div className="relative">
            <video
              ref={videoRef}
              className="block aspect-video w-full bg-black"
              src={DEMO_SRC}
              poster={DEMO_POSTER}
              playsInline
              muted={muted}
              preload="metadata"
              onClick={toggle}
              onPlay={() => setPlaying(true)}
              onPause={() => setPlaying(false)}
              onLoadedMetadata={(event) => setDuration(event.currentTarget.duration)}
              onDurationChange={(event) => setDuration(event.currentTarget.duration)}
              onTimeUpdate={(event) => {
                if (!scrubbing) setCurrent(event.currentTarget.currentTime);
              }}
              onEnded={() => setPlaying(false)}
            >
              Your browser cannot play this video.
            </video>

            {!playing && (
              <button
                type="button"
                onClick={toggle}
                aria-label="Play the demo"
                className="absolute inset-0 flex items-center justify-center bg-black/35 transition-colors hover:bg-black/25 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand"
              >
                <span className="flex h-16 w-16 items-center justify-center rounded-full bg-brand/95 text-brand-foreground shadow-lg">
                  <LuPlay className="ml-1 h-6 w-6" aria-hidden="true" />
                </span>
              </button>
            )}
          </div>

          <div className="flex items-center gap-4 border-t border-border px-4 py-3">
            <button
              type="button"
              onClick={toggle}
              aria-label={playing ? "Pause" : "Play"}
              className="shrink-0 rounded p-1 text-foreground transition-colors hover:text-brand focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {playing ? (
                <LuPause className="h-4 w-4" aria-hidden="true" />
              ) : (
                <LuPlay className="h-4 w-4" aria-hidden="true" />
              )}
            </button>

            <div
              ref={trackRef}
              role="slider"
              tabIndex={0}
              aria-label="Seek"
              aria-valuemin={0}
              aria-valuemax={Math.round(duration)}
              aria-valuenow={Math.round(current)}
              aria-valuetext={`${clock(current)} of ${clock(duration)}`}
              onPointerDown={(event) => {
                event.currentTarget.setPointerCapture(event.pointerId);
                setScrubbing(true);
                onScrub(event);
              }}
              onPointerMove={onScrubMove}
              onPointerUp={(event) => {
                event.currentTarget.releasePointerCapture(event.pointerId);
                setScrubbing(false);
              }}
              onKeyDown={(event) => {
                const video = videoRef.current;
                if (!video) return;
                if (event.key === "ArrowRight") {
                  event.preventDefault();
                  seekTo(video.currentTime + 5);
                } else if (event.key === "ArrowLeft") {
                  event.preventDefault();
                  seekTo(video.currentTime - 5);
                }
              }}
              className="group relative h-6 flex-1 cursor-pointer touch-none select-none focus-visible:outline-none"
            >
              {/* A generous invisible band above and below the line: the line
                  itself is 3px and a 3px target is not one. */}
              <div className="absolute inset-x-0 top-1/2 h-6 -translate-y-1/2" />
              <div className="absolute inset-x-0 top-1/2 h-[3px] -translate-y-1/2 overflow-hidden rounded-full bg-border">
                <div
                  className="h-full bg-brand"
                  style={{ width: `${played * 100}%` }}
                />
              </div>
              {chapters.map((chapter) => (
                <span
                  key={chapter.at}
                  aria-hidden="true"
                  className="absolute top-1/2 h-2 w-[2px] -translate-y-1/2 rounded-full bg-foreground/45"
                  style={{
                    left: `${duration ? (chapter.at / duration) * 100 : 0}%`,
                  }}
                />
              ))}
              <span
                aria-hidden="true"
                className="absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full bg-brand opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100"
                style={{ left: `${played * 100}%` }}
              />
            </div>

            <span className="shrink-0 font-mono text-xs tabular-nums text-muted-foreground">
              {clock(current)} / {clock(duration)}
            </span>

            <button
              type="button"
              onClick={() => setMuted((was) => !was)}
              aria-label={muted ? "Unmute" : "Mute"}
              className="shrink-0 rounded p-1 text-foreground transition-colors hover:text-brand focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {muted ? (
                <LuVolumeX className="h-4 w-4" aria-hidden="true" />
              ) : (
                <LuVolume2 className="h-4 w-4" aria-hidden="true" />
              )}
            </button>
          </div>
        </div>
      </div>

      <ol className="lg:border-l lg:border-border lg:pl-8">
        {chapters.map((chapter, index) => {
          const active = index === activeIndex;
          return (
            <li key={chapter.at} className="border-b border-border last:border-b-0">
              <button
                type="button"
                onClick={() => {
                  seekTo(chapter.at);
                  setCurrent(chapter.at);
                  void videoRef.current?.play().catch(() => {});
                }}
                aria-current={active ? "step" : undefined}
                className={`flex w-full gap-4 py-4 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                  active ? "text-foreground" : "text-foreground-muted hover:text-foreground"
                }`}
              >
                <span
                  className={`mt-0.5 w-9 shrink-0 font-mono text-xs tabular-nums ${
                    active ? "text-brand" : "text-muted-foreground"
                  }`}
                >
                  {clock(chapter.at)}
                </span>
                <span>
                  <span className="block text-sm font-medium">{chapter.title}</span>
                  <span className="mt-1 block text-sm leading-6 text-foreground-muted">
                    {chapter.text}
                  </span>
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </div>
  );
}