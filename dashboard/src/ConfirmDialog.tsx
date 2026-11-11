import { useEffect, useRef } from "react";

export function ConfirmDialog({
  title,
  onConfirm,
  onCancel,
}: Readonly<{
  title: string;
  onConfirm: () => void;
  onCancel: () => void;
}>) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  return (
    <dialog
      ref={dialog}
      className="confirmation-dialog"
      onCancel={onCancel}
      aria-label={title}
    >
      <h2>{title}</h2>
      <p>
        This change applies to future work. Review the values before confirming.
      </p>
      <div className="run-controls">
        <button type="button" className="secondary-action" onClick={onCancel}>
          Cancel
        </button>
        <button type="button" className="primary-action" onClick={onConfirm}>
          Confirm settings change
        </button>
      </div>
    </dialog>
  );
}
