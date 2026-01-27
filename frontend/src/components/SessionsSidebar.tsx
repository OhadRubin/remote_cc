import { useEffect, useState, useCallback } from 'react';

interface Session {
  sessionId: number;
  name: string;
  width: number;
  height: number;
  pid: number;
  createdAt: number;
  attachedCount: number;
}

interface SessionsSidebarProps {
  isOpen: boolean;
  onToggle: () => void;
  onSelectSession: (sessionId: number) => void;
}

export function SessionsSidebar({ isOpen, onToggle, onSelectSession }: SessionsSidebarProps) {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchSessions = useCallback(() => {
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
  }, []);

  useEffect(() => {
    if (isOpen) {
      fetchSessions();
    }
  }, [isOpen, fetchSessions]);

  useEffect(() => {
    if (!isOpen) return;
    const interval = setInterval(fetchSessions, 5000);
    return () => clearInterval(interval);
  }, [isOpen, fetchSessions]);

  const handleKillSession = async (sessionId: number, sessionName: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm(`Kill session "${sessionName}"? This will terminate the shell process.`)) {
      return;
    }
    try {
      const response = await fetch(`/api/sessions/${sessionId}`, {
        method: 'DELETE',
      });
      if (response.ok) {
        fetchSessions();
      } else {
        const data = await response.json();
        alert(data.error || 'Failed to kill session');
      }
    } catch (err) {
      console.error('Failed to kill session:', err);
      alert('Failed to kill session');
    }
  };

  const handleSelectSession = (sessionId: number) => {
    onSelectSession(sessionId);
  };

  return (
    <>
      <div className={`sidebar ${isOpen ? 'open' : 'collapsed'}`}>
        <div className="sidebar-header">
          <h3>Sessions</h3>
          <button className="sidebar-close" onClick={onToggle}>
            &times;
          </button>
        </div>
        <div className="sidebar-content">
          {loading && <div className="sidebar-message">Loading...</div>}
          {error && <div className="sidebar-message error">{error}</div>}
          {!loading && !error && sessions.length === 0 && (
            <div className="sidebar-message">No sessions found</div>
          )}
          {!loading &&
            !error &&
            sessions.map((s) => {
              const isOrphaned = s.attachedCount === 0;
              const date = new Date(s.createdAt * 1000);
              const timeStr = date.toLocaleTimeString();
              return (
                <div
                  key={s.sessionId}
                  className={`sidebar-session-item ${isOrphaned ? 'orphaned' : ''}`}
                  onClick={() => handleSelectSession(s.sessionId)}
                >
                  <div className="session-info">
                    <div className="session-header">
                      {isOrphaned && <span className="orphan-indicator" title="No clients attached">&#9888;</span>}
                      <span className={`status-dot ${isOrphaned ? 'inactive' : 'active'}`}></span>
                      <span className="session-name">{s.name}</span>
                    </div>
                    <div className="session-details">
                      {s.attachedCount} attached | {timeStr}
                    </div>
                  </div>
                  <button
                    className="delete-btn"
                    onClick={(e) => handleKillSession(s.sessionId, s.name, e)}
                    title="Kill session"
                  >
                    &#128465;
                  </button>
                </div>
              );
            })}
        </div>
        <div className="sidebar-footer">
          <button className="settings-btn" onClick={() => alert('Settings coming soon')}>
            &#9881; Settings
          </button>
        </div>
      </div>
      {isOpen && <div className="sidebar-backdrop" onClick={onToggle}></div>}
    </>
  );
}
