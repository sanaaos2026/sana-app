import { useState } from "react";

export function SanaStartScreen() {
  const [isPressed, setIsPressed] = useState(false);

  return (
    <main className="sana-start" dir="rtl">
      <div className="sana-grain" aria-hidden="true" />
      <section className="sana-frame" aria-label="سنا">
        <header className="sana-header">
          <div className="sana-wordmark" aria-label="سنا">
            <span className="sana-mark" aria-hidden="true">
              <i />
              <i />
              <i />
            </span>
            <span>سَنا</span>
          </div>
          <span className="sana-status" aria-hidden="true" />
        </header>

        <div className={`sana-center ${isPressed ? "is-pressed" : ""}`}>
          <div className="sana-orbit" aria-hidden="true">
            <span />
          </div>
          <div className="sana-core" aria-hidden="true">
            <span />
          </div>
          <button
            className="sana-action"
            type="button"
            aria-label="ابدأ الآن"
            onClick={() => setIsPressed(true)}
            onAnimationEnd={() => setIsPressed(false)}
          >
            <span>ابدأ الآن</span>
          </button>
        </div>

        <footer className="sana-footer" aria-hidden="true">
          <span />
          <span />
          <span />
        </footer>
      </section>
    </main>
  );
}

export default SanaStartScreen;