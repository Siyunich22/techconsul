"use client";

import { Upload } from "lucide-react";
import { useRef } from "react";

import { Button } from "@/components/ui/button";

export function FileUploadButton({
  accept,
  label,
  pending,
  disabled,
  onFile,
}: {
  accept: string;
  label: string;
  pending?: boolean;
  disabled?: boolean;
  onFile: (file: File) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <>
      <input
        ref={input}
        type="file"
        accept={accept}
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) onFile(file);
          e.target.value = "";
        }}
      />
      <Button type="button" variant="outline" size="sm" disabled={pending || disabled} onClick={() => input.current?.click()}>
        <Upload /> {pending ? "…" : label}
      </Button>
    </>
  );
}
