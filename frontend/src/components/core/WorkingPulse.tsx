function WorkingPulse() {
  return (
    <span className="inline-flex items-end gap-0.5" aria-hidden="true">
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-brand/90 [animation-duration:900ms]" />
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-brand/70 [animation-duration:900ms] [animation-delay:150ms]" />
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-brand/50 [animation-duration:900ms] [animation-delay:300ms]" />
    </span>
  );
}

export default WorkingPulse;
