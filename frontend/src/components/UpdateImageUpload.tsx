import { useRef } from "react";
import { toast } from "react-hot-toast";
import { Cross2Icon } from "@radix-ui/react-icons";
import { LuPlus } from "react-icons/lu";

const MAX_UPDATE_IMAGES = 5;

// Helper function to convert file to data URL
function fileToDataURL(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = (error) => reject(error);
    reader.readAsDataURL(file);
  });
}

interface Props {
  updateImages: string[];
  setUpdateImages: (images: string[]) => void;
}

export function UpdateImagePreview({ updateImages, setUpdateImages }: Props) {
  const removeImage = (index: number) => {
    const newImages = updateImages.filter((_, i) => i !== index);
    setUpdateImages(newImages);
  };

  if (updateImages.length === 0) return null;

  return (
    <div className="px-3 pt-3">
      <div className="flex flex-wrap gap-2 py-1">
        {updateImages.map((image, index) => (
          <div
            key={index}
            className="group relative flex-shrink-0 overflow-visible"
          >
            <div className="flex h-14 w-14 items-center justify-center rounded-lg border border-border bg-background p-1">
              <img
                src={image}
                alt={`Reference ${index + 1}`}
                className="max-h-full max-w-full object-contain"
              />
            </div>
            <button
              type="button"
              onClick={() => removeImage(index)}
              aria-label={`Remove reference ${index + 1}`}
              className="absolute -right-1 -top-1 z-10 flex h-5 w-5 items-center justify-center rounded-full border border-card bg-foreground text-background opacity-0 transition-opacity hover:bg-danger group-hover:opacity-100 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <Cross2Icon className="h-2.5 w-2.5" aria-hidden="true" />
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

function UpdateImageUpload({ updateImages, setUpdateImages }: Props) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const remaining = Math.max(0, MAX_UPDATE_IMAGES - updateImages.length);
  const isAtLimit = remaining === 0;


  const handleButtonClick = () => {
    if (isAtLimit) {
      toast.error(
        `You’ve reached the limit of ${MAX_UPDATE_IMAGES} reference images. Remove one to add another.`
      );
      return;
    }
    fileInputRef.current?.click();
  };

  const handleFileInputChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (files) {
      try {
        if (updateImages.length >= MAX_UPDATE_IMAGES) {
          toast.error(
            `You’ve reached the limit of ${MAX_UPDATE_IMAGES} reference images. Remove one to add another.`
          );
          return;
        }

        const remainingSlots = MAX_UPDATE_IMAGES - updateImages.length;
        let filesToAdd = Array.from(files);
        if (filesToAdd.length > remainingSlots) {
          toast.error(
            `Only ${remainingSlots} more image${
              remainingSlots === 1 ? "" : "s"
            } will be added to stay within the ${MAX_UPDATE_IMAGES}-image limit.`
          );
          filesToAdd = filesToAdd.slice(0, remainingSlots);
        }

        const newImagePromises = filesToAdd.map((file) => fileToDataURL(file));
        const newImages = await Promise.all(newImagePromises);
        setUpdateImages([...updateImages, ...newImages]);
        e.target.value = "";
      } catch (error) {
        toast.error("Error reading image files");
        console.error("Error reading files:", error);
      }
    }
  };

  return (
    <div className="relative inline-block">
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept="image/png,image/jpeg"
        onChange={handleFileInputChange}
        className="hidden"
      />
      <button
        type="button"
        onClick={handleButtonClick}
        disabled={isAtLimit}
        aria-label="Add images"
        className={`rounded-lg p-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
          isAtLimit
            ? "cursor-not-allowed text-muted-foreground/40"
            : "text-muted-foreground hover:bg-accent hover:text-foreground"
        }`}
        title={
          isAtLimit ? `Limit reached (${MAX_UPDATE_IMAGES})` : "Add images"
        }
      >
        <LuPlus className="h-[18px] w-[18px]" aria-hidden="true" />
      </button>
    </div>
  );
}

export default UpdateImageUpload;
