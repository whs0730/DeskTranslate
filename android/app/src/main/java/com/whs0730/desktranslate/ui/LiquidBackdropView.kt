package com.whs0730.desktranslate.ui

import android.content.Context
import android.content.res.Configuration
import android.graphics.Bitmap
import android.graphics.BitmapShader
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.LinearGradient
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RadialGradient
import android.graphics.Rect
import android.graphics.Shader
import android.util.AttributeSet
import android.view.View
import androidx.core.graphics.createBitmap
import kotlin.math.max
import kotlin.math.min

/**
 * Full-screen fluid optical backdrop inspired by Apple WWDC Liquid Glass.
 *
 * Maintains a half-resolution offscreen [Bitmap] and a screen-space [BitmapShader]
 * so every foreground [LiquidGlassHost] can sample, refract, and disperse the
 * exact underlying colors as the user scrolls or animates controls over it.
 */
class LiquidBackdropView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0,
) : View(context, attrs, defStyleAttr) {

    private var cachedBitmap: Bitmap? = null
    private var cachedShader: BitmapShader? = null
    private val bitmapPaint = Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG)
    private val destRect = Rect()
    private val shaderMatrix = Matrix()
    private val listeners = mutableListOf<() -> Unit>()

    val isDarkTheme: Boolean
        get() = (resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK) ==
            Configuration.UI_MODE_NIGHT_YES

    val backdropBitmap: Bitmap?
        get() = cachedBitmap

    /** Screen-space shader aligned 1:1 with this view's pixel dimensions. */
    val backdropShader: BitmapShader?
        get() = cachedShader

    fun addOnBackdropChangedListener(listener: () -> Unit) {
        if (!listeners.contains(listener)) {
            listeners.add(listener)
        }
    }

    fun removeOnBackdropChangedListener(listener: () -> Unit) {
        listeners.remove(listener)
    }

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        if (w > 0 && h > 0) {
            destRect.set(0, 0, w, h)
            rebuildBackdrop(w, h)
        }
    }

    override fun onDraw(canvas: Canvas) {
        val bmp = cachedBitmap ?: return
        canvas.drawBitmap(bmp, null, destRect, bitmapPaint)
    }

    override fun onDetachedFromWindow() {
        cachedBitmap?.recycle()
        cachedBitmap = null
        cachedShader = null
        super.onDetachedFromWindow()
    }

    private fun rebuildBackdrop(viewW: Int, viewH: Int) {
        val scale = 0.5f
        val bmpW = max(2, (viewW * scale).toInt())
        val bmpH = max(2, (viewH * scale).toInt())

        cachedBitmap?.recycle()
        val bmp = createBitmap(bmpW, bmpH, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(bmp)

        if (isDarkTheme) {
            drawDarkLiquidCanvas(canvas, bmpW.toFloat(), bmpH.toFloat())
        } else {
            drawLightLiquidCanvas(canvas, bmpW.toFloat(), bmpH.toFloat())
        }

        val shader = BitmapShader(bmp, Shader.TileMode.CLAMP, Shader.TileMode.CLAMP)
        shaderMatrix.reset()
        shaderMatrix.setScale(viewW.toFloat() / bmpW.toFloat(), viewH.toFloat() / bmpH.toFloat())
        shader.setLocalMatrix(shaderMatrix)

        cachedBitmap = bmp
        cachedShader = shader
        invalidate()
        listeners.forEach { it() }
    }

    /**
     * Luminous airy glacier-blue, soft violet, and mint fluid ribbons tailored
     * so transparent glass edges visibly bend and disperse light while keeping
     * dark ink typography crisp.
     */
    private fun drawLightLiquidCanvas(canvas: Canvas, w: Float, h: Float) {
        val paint = Paint(Paint.ANTI_ALIAS_FLAG)
        val diag = min(w, h)

        paint.shader = LinearGradient(
            0f,
            0f,
            w,
            h,
            intArrayOf(
                0xFFD5E7F8.toInt(),
                0xFFE8EEF8.toInt(),
                0xFFDCECEB.toInt(),
                0xFFCEE3F5.toInt(),
            ),
            floatArrayOf(0f, 0.38f, 0.72f, 1f),
            Shader.TileMode.CLAMP,
        )
        canvas.drawRect(0f, 0f, w, h, paint)

        drawOrb(canvas, w * 0.14f, h * 0.12f, diag * 0.78f, 0x9938A3FF.toInt(), 0x2638A3FF)
        drawOrb(canvas, w * 0.90f, h * 0.24f, diag * 0.76f, 0x948470FF.toInt(), 0x228470FF)
        drawOrb(canvas, w * 0.12f, h * 0.56f, diag * 0.80f, 0x9C2BD4AC.toInt(), 0x242BD4AC)
        drawOrb(canvas, w * 0.86f, h * 0.72f, diag * 0.82f, 0x9936B6FA.toInt(), 0x2436B6FA)
        drawOrb(canvas, w * 0.48f, h * 0.94f, diag * 0.72f, 0x886D9CFF.toInt(), 0x1E6D9CFF)

        drawRibbon(
            canvas = canvas,
            w = w,
            h = h,
            yStart = h * 0.16f,
            yMid1 = h * 0.06f,
            yMid2 = h * 0.34f,
            yEnd = h * 0.22f,
            strokeWidth = diag * 0.16f,
            startColor = 0x883CA4FF.toInt(),
            midColor = 0xB0FFFFFF.toInt(),
            endColor = 0x888C75FF.toInt(),
        )
        drawRibbon(
            canvas = canvas,
            w = w,
            h = h,
            yStart = h * 0.44f,
            yMid1 = h * 0.58f,
            yMid2 = h * 0.32f,
            yEnd = h * 0.50f,
            strokeWidth = diag * 0.18f,
            startColor = 0x8826CFA9.toInt(),
            midColor = 0xA6D7F8FF.toInt(),
            endColor = 0x80638BFF.toInt(),
        )
        drawRibbon(
            canvas = canvas,
            w = w,
            h = h,
            yStart = h * 0.78f,
            yMid1 = h * 0.64f,
            yMid2 = h * 0.88f,
            yEnd = h * 0.72f,
            strokeWidth = diag * 0.15f,
            startColor = 0x8232B5FF.toInt(),
            midColor = 0xA8FFFFFF.toInt(),
            endColor = 0x8038D9B5.toInt(),
        )

        drawOrb(canvas, w * 0.52f, h * 0.30f, diag * 0.42f, 0x99FFFFFF.toInt(), 0x00FFFFFF)
        drawOrb(canvas, w * 0.38f, h * 0.68f, diag * 0.38f, 0x85FFFFFF.toInt(), 0x00FFFFFF)
    }

    /**
     * Deep obsidian glass canvas with luminous cyan, indigo, violet, and aurora-mint
     * streams so dark-mode liquid glass glows with crisp refraction and specular arcs.
     */
    private fun drawDarkLiquidCanvas(canvas: Canvas, w: Float, h: Float) {
        val paint = Paint(Paint.ANTI_ALIAS_FLAG)
        val diag = min(w, h)

        paint.shader = LinearGradient(
            0f,
            0f,
            w,
            h,
            intArrayOf(
                0xFF0A121B.toInt(),
                0xFF111B27.toInt(),
                0xFF0D181E.toInt(),
                0xFF081018.toInt(),
            ),
            floatArrayOf(0f, 0.36f, 0.70f, 1f),
            Shader.TileMode.CLAMP,
        )
        canvas.drawRect(0f, 0f, w, h, paint)

        drawOrb(canvas, w * 0.14f, h * 0.13f, diag * 0.78f, 0xA61F74F0.toInt(), 0x1A1F74F0)
        drawOrb(canvas, w * 0.88f, h * 0.25f, diag * 0.78f, 0x996F48F2.toInt(), 0x186F48F2)
        drawOrb(canvas, w * 0.16f, h * 0.56f, diag * 0.82f, 0x9C12BA8E.toInt(), 0x1812BA8E)
        drawOrb(canvas, w * 0.86f, h * 0.74f, diag * 0.82f, 0x9C0C93E4.toInt(), 0x180C93E4)
        drawOrb(canvas, w * 0.46f, h * 0.94f, diag * 0.72f, 0x884E5EE8.toInt(), 0x144E5EE8)

        drawRibbon(
            canvas = canvas,
            w = w,
            h = h,
            yStart = h * 0.17f,
            yMid1 = h * 0.07f,
            yMid2 = h * 0.34f,
            yEnd = h * 0.22f,
            strokeWidth = diag * 0.15f,
            startColor = 0x882894FF.toInt(),
            midColor = 0x66C7F3FF,
            endColor = 0x887E5CFF.toInt(),
        )
        drawRibbon(
            canvas = canvas,
            w = w,
            h = h,
            yStart = h * 0.46f,
            yMid1 = h * 0.60f,
            yMid2 = h * 0.34f,
            yEnd = h * 0.52f,
            strokeWidth = diag * 0.17f,
            startColor = 0x8C19D4A6.toInt(),
            midColor = 0x5CC4EEDB,
            endColor = 0x803E7BFF.toInt(),
        )
        drawRibbon(
            canvas = canvas,
            w = w,
            h = h,
            yStart = h * 0.79f,
            yMid1 = h * 0.65f,
            yMid2 = h * 0.89f,
            yEnd = h * 0.73f,
            strokeWidth = diag * 0.15f,
            startColor = 0x8218A6F8.toInt(),
            midColor = 0x5CDDF6FF,
            endColor = 0x8025D4A5.toInt(),
        )
    }

    private fun drawOrb(
        canvas: Canvas,
        cx: Float,
        cy: Float,
        radius: Float,
        innerColor: Int,
        midColor: Int,
    ) {
        val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            shader = RadialGradient(
                cx,
                cy,
                radius,
                intArrayOf(innerColor, midColor, Color.TRANSPARENT),
                floatArrayOf(0f, 0.52f, 1f),
                Shader.TileMode.CLAMP,
            )
        }
        canvas.drawCircle(cx, cy, radius, paint)
    }

    private fun drawRibbon(
        canvas: Canvas,
        w: Float,
        h: Float,
        yStart: Float,
        yMid1: Float,
        yMid2: Float,
        yEnd: Float,
        strokeWidth: Float,
        startColor: Int,
        midColor: Int,
        endColor: Int,
    ) {
        val path = Path().apply {
            moveTo(-w * 0.12f, yStart)
            cubicTo(w * 0.30f, yMid1, w * 0.68f, yMid2, w * 1.12f, yEnd)
        }
        val passes = listOf(1.9f to 0.28f, 1.35f to 0.42f, 0.8f to 0.62f)
        for ((widthMul, alphaMul) in passes) {
            val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
                style = Paint.Style.STROKE
                strokeCap = Paint.Cap.ROUND
                this.strokeWidth = strokeWidth * widthMul
                shader = LinearGradient(
                    0f,
                    yStart,
                    w,
                    yEnd,
                    intArrayOf(
                        scaleAlpha(startColor, alphaMul),
                        scaleAlpha(midColor, alphaMul),
                        scaleAlpha(endColor, alphaMul),
                    ),
                    floatArrayOf(0f, 0.5f, 1f),
                    Shader.TileMode.CLAMP,
                )
            }
            canvas.drawPath(path, paint)
        }
    }

    private fun scaleAlpha(color: Int, factor: Float): Int {
        val a = (Color.alpha(color) * factor).toInt().coerceIn(0, 255)
        return Color.argb(a, Color.red(color), Color.green(color), Color.blue(color))
    }
}
