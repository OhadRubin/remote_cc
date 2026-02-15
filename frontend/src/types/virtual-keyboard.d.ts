interface VirtualKeyboard extends EventTarget {
  overlaysContent: boolean;
  boundingRect: DOMRect;
  show(): void;
  hide(): void;
  addEventListener(type: 'geometrychange', listener: (e: Event) => void): void;
  removeEventListener(type: 'geometrychange', listener: (e: Event) => void): void;
}

interface Navigator {
  virtualKeyboard?: VirtualKeyboard;
}
