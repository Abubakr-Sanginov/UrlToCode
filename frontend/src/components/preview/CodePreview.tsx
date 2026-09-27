import { useRef, useEffect } from "react";

interface Props {
  code: string;
}

function CodePreview({ code }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollLeft = scrollRef.current.scrollWidth;
    }
  }, [code]);

  return (
    <div
      ref={scrollRef}
      className="my-4 flex w-full overflow-x-auto whitespace-nowrap rounded-md border border-border bg-muted px-2 py-1 font-mono text-[10px] text-muted-foreground"
    >
      {code}
    </div>
  );
}

export default CodePreview;
