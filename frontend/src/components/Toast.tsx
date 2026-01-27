import { useToast, type Toast as ToastType } from '../context/ToastContext';

function ToastItem({ toast, onDismiss }: { toast: ToastType; onDismiss: () => void }) {
  return (
    <div className={`toast toast-${toast.type}`} onClick={onDismiss}>
      <span className="toast-message">{toast.message}</span>
      <span className="toast-dismiss">&times;</span>
    </div>
  );
}

export function ToastContainer() {
  const { toasts, dismissToast } = useToast();

  if (toasts.length === 0) return null;

  return (
    <div className="toast-container">
      {toasts.map((toast) => (
        <ToastItem
          key={toast.id}
          toast={toast}
          onDismiss={() => dismissToast(toast.id)}
        />
      ))}
    </div>
  );
}
