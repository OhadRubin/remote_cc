import { useEffect, useState } from 'react';

interface Session {
  sessionId: number;
  name: string;
  width: number;
  height: number;
  pid: number;
  createdAt: number;
  attachedCount: number;
}

interface SessionsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSelectSession: (sessionId: number) => void;
}

export function SessionsModal({ isOpen, onClose, onSelectSession }: SessionsModalProps) {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen) return;

    setLoading(true);
    setError(null);

    fetch('/api/sessions')
      .then((response) => response.json())
      .then((data) => {
        const mapped = data.sessions.map((s: {
          id: number;
          name: string;
          width: number;
          height: number;
          pid: number;
          created_at: number;
          attached_count: number;
        }) => ({
          sessionId: s.id,
          name: s.name,
          width: s.width,
          height: s.height,
          pid: s.pid,
          createdAt: s.created_at,
          attachedCount: s.attached_count,
        }));
        setSessions(mapped);
        setLoading(false);
      })
      .catch((e) => {
        console.error('Failed to fetch sessions:', e);
        setError('Failed to load sessions.');
        setLoading(false);
      });
  }, [isOpen]);

  if (!isOpen) return null;

  const handleClick = (sessionId: number) => {
    onClose();
    onSelectSession(sessionId);
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Existing Sessions</h2>
          <button className="modal-close" onClick={onClose}>
            &times;
          </button>
        </div>
        <div id="sessions-list">
          {loading && <div className="no-sessions">Loading...</div>}
          {error && <div className="no-sessions">{error}</div>}
          {!loading && !error && sessions.length === 0 && (
            <div className="no-sessions">No existing sessions. Create a new tab first.</div>
          )}
          {!loading &&
            !error &&
            sessions.map((s) => {
              const date = new Date(s.createdAt * 1000);
              const timeStr = date.toLocaleTimeString();
              return (
                <div
                  key={s.sessionId}
                  className="session-item"
                  onClick={() => handleClick(s.sessionId)}
                >
                  <div>
                    <div className="session-name">{s.name}</div>
                    <div className="session-meta">
                      ID: {s.sessionId} | {s.width}x{s.height} | PID: {s.pid}
                    </div>
                  </div>
                  <div className="session-meta">
                    {s.attachedCount} attached | {timeStr}
                  </div>
                </div>
              );
            })}
        </div>
      </div>
    </div>
  );
}
