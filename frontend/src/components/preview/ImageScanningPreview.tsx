interface Props {
  imageUrl: string;
}

function ImageScanningPreview({ imageUrl }: Props) {
  return (
    <div
      className="flex h-full min-h-0 items-center justify-center overflow-hidden bg-canvas p-5 sm:p-8"
      data-testid="image-scanning-preview"
    >
      <div className="relative w-full max-w-5xl">
        <div className="relative overflow-hidden rounded-2xl border border-border bg-card p-2 shadow-raised">
          <div className="relative max-h-[72vh] overflow-hidden rounded-xl bg-muted">
            <img
              src={imageUrl}
              alt="Uploaded screenshot being analyzed"
              className="block max-h-[72vh] w-full object-contain"
            />
            {/* Soft band, no glow: a progress hint rather than a scanner beam. */}
            <div className="scan-sweep pointer-events-none absolute inset-x-0 top-0 h-24">
              <div className="absolute inset-0 bg-gradient-to-b from-transparent to-brand/12" />
              <div className="absolute inset-x-[4%] bottom-0 h-px bg-gradient-to-r from-transparent via-brand/60 to-transparent" />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default ImageScanningPreview;
