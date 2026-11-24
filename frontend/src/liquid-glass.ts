import { createLiquidGlass, QUALITY_PRESETS, type LiquidGlassInstance } from '@librecosta/liquid-glass';

let glasses: LiquidGlassInstance[] = [];

export function setLiquidGlass(enabled: boolean): void {
  if (enabled && glasses.length === 0) {
    // 库的 ResizeObserver 会在隐藏元素出现后按实际尺寸生成滤镜。
    glasses = [...document.querySelectorAll<HTMLElement>('[data-glass-surface]')].map((element) =>
      createLiquidGlass(element, { ...QUALITY_PRESETS.Balanced, settle: 150 })
    );
    document.documentElement.dataset.liquidGlass = String(glasses.length);
  } else if (!enabled && glasses.length > 0) {
    glasses.forEach((glass) => glass.destroy());
    glasses = [];
    delete document.documentElement.dataset.liquidGlass;
  }
}

/** 指针在场：把鼠标位置写进 --gx/--gy，让 CSS 的高光跟着指针走（每帧最多写一个元素）。 */
export function trackGlassPointer(): void {
  let frame = 0;
  let pending: { element: HTMLElement; x: string; y: string } | null = null;

  const flush = (): void => {
    frame = 0;
    if (pending === null) {
      return;
    }
    pending.element.style.setProperty('--gx', pending.x);
    pending.element.style.setProperty('--gy', pending.y);
    pending = null;
  };

  document.addEventListener(
    'pointermove',
    (event) => {
      if (!document.documentElement.classList.contains('appearance-liquid')) {
        return;
      }
      const target = event.target;
      if (!(target instanceof Element)) {
        return;
      }
      const surface = target.closest<HTMLElement>('[data-glass-surface]');
      if (surface === null) {
        return;
      }
      const bounds = surface.getBoundingClientRect();
      if (bounds.width === 0 || bounds.height === 0) {
        return;
      }
      const x = `${Math.round(((event.clientX - bounds.left) / bounds.width) * 100)}%`;
      const y = `${Math.round(((event.clientY - bounds.top) / bounds.height) * 100)}%`;
      pending = { element: surface, x, y };
      if (frame === 0) {
        frame = window.requestAnimationFrame(flush);
      }
    },
    { passive: true }
  );
}
