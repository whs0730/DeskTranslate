package com.whs0730.desktranslate.ui

import android.content.Context
import android.content.res.Configuration
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.LinearGradient
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import android.graphics.RuntimeShader
import android.graphics.Shader
import android.graphics.SweepGradient
import android.os.Build
import android.util.AttributeSet
import android.view.View
import android.widget.FrameLayout
import android.widget.LinearLayout
import androidx.annotation.RequiresApi
import androidx.core.graphics.withClip
import kotlin.math.min

/**
 * Optical presets for Liquid Glass surfaces across the app.
 */
enum class LiquidGlassPreset {
    /** Main translucent content panels (source, result, settings cards). */
    PANEL,

    /** Recessed pill track behind the floating direction lens. */
    TRACK,

    /** Floating convex lens thumb for the direction toggle. */
    THUMB,

    /** Circular / pill neutral glass button (e.g. top-right Settings button). */
    BUTTON_CLEAR,

    /** Compact mint-tinted liquid glass action pill (Translate / Save button). */
    BUTTON_MINT,

    /** Subtle inner sub-card / input field inside a glass panel. */
    FIELD,
}

interface LiquidGlassHost {
    fun bindBackdrop(backdrop: LiquidBackdropView)
    fun setGlassPreset(preset: LiquidGlassPreset)
    fun setGlassCornerRadiusDp(radiusDp: Float)
    fun refreshGlass()
}

/**
 * Shared WWDC-inspired Liquid Glass renderer.
 *
 * On Android 13+ (API 33+), runs a real GPU AGSL [RuntimeShader] that samples the
 * underlying [LiquidBackdropView] in exact screen coordinates, bends UV coordinates
 * along the rounded-box SDF bezel normal (convex lens refraction), applies subtle
 * RGB chromatic dispersion along the rim, performs a lightweight 9-tap optical blur
 * with vibrancy boost, and adds Apple-style 45° dual-lobe specular edge highlights.
 *
 * On API 26–32 (or if shader compilation ever fails), gracefully falls back to a
 * translucent sampled-backdrop + dual-lobe sweep-gradient rim renderer.
 */
class LiquidGlassSurface(private val targetView: View) {

    private var backdropView: LiquidBackdropView? = null
    private var preset: LiquidGlassPreset = LiquidGlassPreset.PANEL
    private var customCornerRadiusDp: Float = -1f
    private var isPressedState: Boolean = false
    private var isEnabledState: Boolean = true

    private val density = targetView.resources.displayMetrics.density
    private val rect = RectF()
    private val clipPath = Path()
    private val viewLoc = IntArray(2)
    private val backdropLoc = IntArray(2)

    private val shaderPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val fallbackFillPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val fallbackRimPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
    }
    private val fallbackGlossPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val fallbackBackdropPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val shadowPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val fallbackMatrix = Matrix()

    private var agslShader: Any? = null
    private var agslInitFailed = false

    private val backdropListener: () -> Unit = {
        targetView.invalidate()
    }

    private val isDarkTheme: Boolean
        get() = backdropView?.isDarkTheme
            ?: ((targetView.resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK) ==
                Configuration.UI_MODE_NIGHT_YES)

    fun bindBackdrop(backdrop: LiquidBackdropView) {
        if (backdropView === backdrop) {
            targetView.invalidate()
            return
        }
        backdropView?.removeOnBackdropChangedListener(backdropListener)
        backdropView = backdrop
        backdrop.addOnBackdropChangedListener(backdropListener)
        targetView.invalidate()
    }

    fun detachBackdrop() {
        backdropView?.removeOnBackdropChangedListener(backdropListener)
        backdropView = null
    }

    fun setPreset(newPreset: LiquidGlassPreset) {
        if (preset != newPreset) {
            preset = newPreset
            targetView.invalidate()
        }
    }

    fun setCornerRadiusDp(radiusDp: Float) {
        if (customCornerRadiusDp != radiusDp) {
            customCornerRadiusDp = radiusDp
            targetView.invalidate()
        }
    }

    fun updateDrawableState(pressed: Boolean, enabled: Boolean) {
        if (isPressedState != pressed || isEnabledState != enabled) {
            isPressedState = pressed
            isEnabledState = enabled
            targetView.invalidate()
        }
    }

    fun draw(canvas: Canvas) {
        val w = targetView.width.toFloat()
        val h = targetView.height.toFloat()
        if (w <= 1f || h <= 1f) return

        val radius = resolveCornerRadiusPx(w, h)
        rect.set(0f, 0f, w, h)

        val backdrop = backdropView
        val backdropShader = backdrop?.backdropShader
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            backdrop != null &&
            backdropShader != null &&
            !agslInitFailed
        ) {
            if (drawAgslGlass(canvas, w, h, radius, backdrop, backdropShader)) {
                return
            }
        }

        drawFallbackGlass(canvas, w, h, radius, backdrop)
    }

    private fun resolveCornerRadiusPx(w: Float, h: Float): Float {
        val maxR = min(w, h) * 0.5f
        if (customCornerRadiusDp >= 0f) {
            return min(customCornerRadiusDp * density, maxR)
        }
        return when (preset) {
            LiquidGlassPreset.PANEL -> min(26f * density, maxR)
            LiquidGlassPreset.TRACK -> maxR
            LiquidGlassPreset.THUMB -> maxR
            LiquidGlassPreset.BUTTON_CLEAR -> maxR
            LiquidGlassPreset.BUTTON_MINT -> maxR
            LiquidGlassPreset.FIELD -> min(16f * density, maxR)
        }
    }

    @RequiresApi(Build.VERSION_CODES.TIRAMISU)
    private fun drawAgslGlass(
        canvas: Canvas,
        w: Float,
        h: Float,
        radius: Float,
        backdrop: LiquidBackdropView,
        backdropShader: Shader,
    ): Boolean {
        val runtimeShader = getOrCreateAgslShader() ?: return false

        targetView.getLocationInWindow(viewLoc)
        backdrop.getLocationInWindow(backdropLoc)
        val originX = (viewLoc[0] - backdropLoc[0]).toFloat()
        val originY = (viewLoc[1] - backdropLoc[1]).toFloat()

        val dark = isDarkTheme
        val spec = opticsFor(preset, dark)

        // Subtle ambient contact shadow behind elevated glass pieces
        if (spec.shadowAlpha > 0f) {
            shadowPaint.color = Color.argb((spec.shadowAlpha * 255).toInt(), 8, 18, 28)
            rect.set(0f, 2f * density, w, h + 2f * density)
            canvas.drawRoundRect(rect, radius, radius, shadowPaint)
            rect.set(0f, 0f, w, h)
        }

        runtimeShader.setInputShader("backdrop", backdropShader)
        runtimeShader.setFloatUniform("resolution", w, h)
        runtimeShader.setFloatUniform("viewOrigin", originX, originY)
        runtimeShader.setFloatUniform("cornerRadius", radius)
        runtimeShader.setFloatUniform("bezelWidth", spec.bezelDp * density)
        runtimeShader.setFloatUniform("refraction", spec.refractionDp * density)
        runtimeShader.setFloatUniform("dispersion", spec.dispersion)
        runtimeShader.setFloatUniform("blurRadius", spec.blurDp * density)
        runtimeShader.setFloatUniform(
            "tintColor",
            Color.red(spec.tintColor) / 255f,
            Color.green(spec.tintColor) / 255f,
            Color.blue(spec.tintColor) / 255f,
            Color.alpha(spec.tintColor) / 255f,
        )
        runtimeShader.setFloatUniform("specularStrength", spec.specular)
        runtimeShader.setFloatUniform("vibrancy", spec.vibrancy)
        runtimeShader.setFloatUniform("darkMode", if (dark) 1f else 0f)
        runtimeShader.setFloatUniform("pressed", if (isPressedState) 1f else 0f)
        runtimeShader.setFloatUniform("disabled", if (isEnabledState) 0f else 1f)

        shaderPaint.shader = runtimeShader
        canvas.drawRoundRect(rect, radius, radius, shaderPaint)
        return true
    }

    @RequiresApi(Build.VERSION_CODES.TIRAMISU)
    private fun getOrCreateAgslShader(): RuntimeShader? {
        val existing = agslShader as? RuntimeShader
        if (existing != null) return existing
        return try {
            RuntimeShader(LIQUID_GLASS_AGSL).also { agslShader = it }
        } catch (_: Throwable) {
            agslInitFailed = true
            null
        }
    }

    private fun drawFallbackGlass(
        canvas: Canvas,
        w: Float,
        h: Float,
        radius: Float,
        backdrop: LiquidBackdropView?,
    ) {
        val dark = isDarkTheme
        val spec = opticsFor(preset, dark)

        clipPath.reset()
        clipPath.addRoundRect(rect, radius, radius, Path.Direction.CW)

        canvas.withClip(clipPath) {
            // Sample underlying backdrop aligned to view window position
            val shader = backdrop?.backdropShader
            if (backdrop != null && shader != null) {
                targetView.getLocationInWindow(viewLoc)
                backdrop.getLocationInWindow(backdropLoc)
                val dx = -(viewLoc[0] - backdropLoc[0]).toFloat()
                val dy = -(viewLoc[1] - backdropLoc[1]).toFloat()
                fallbackMatrix.reset()
                val bmp = backdrop.backdropBitmap
                if (bmp != null) {
                    fallbackMatrix.setScale(
                        backdrop.width.toFloat() / bmp.width.toFloat(),
                        backdrop.height.toFloat() / bmp.height.toFloat(),
                    )
                }
                fallbackMatrix.postTranslate(dx, dy)
                shader.setLocalMatrix(fallbackMatrix)
                fallbackBackdropPaint.shader = shader
                drawRect(rect, fallbackBackdropPaint)

                // Restore backdrop shader's neutral screen-space matrix
                if (bmp != null) {
                    fallbackMatrix.reset()
                    fallbackMatrix.setScale(
                        backdrop.width.toFloat() / bmp.width.toFloat(),
                        backdrop.height.toFloat() / bmp.height.toFloat(),
                    )
                    shader.setLocalMatrix(fallbackMatrix)
                }
            }

            // Translucent glass tint
            val tintAlpha = (Color.alpha(spec.tintColor) * if (isEnabledState) 1f else 0.65f).toInt()
            fallbackFillPaint.shader = LinearGradient(
                0f,
                0f,
                0f,
                h,
                Color.argb(
                    (tintAlpha * 1.15f).toInt().coerceAtMost(255),
                    Color.red(spec.tintColor),
                    Color.green(spec.tintColor),
                    Color.blue(spec.tintColor),
                ),
                Color.argb(
                    (tintAlpha * 0.72f).toInt().coerceAtMost(255),
                    Color.red(spec.tintColor),
                    Color.green(spec.tintColor),
                    Color.blue(spec.tintColor),
                ),
                Shader.TileMode.CLAMP,
            )
            drawRect(rect, fallbackFillPaint)

            // Top Fresnel gloss band
            fallbackGlossPaint.shader = LinearGradient(
                0f,
                0f,
                0f,
                h * 0.42f,
                Color.argb(if (dark) 44 else 92, 255, 255, 255),
                Color.TRANSPARENT,
                Shader.TileMode.CLAMP,
            )
            drawRect(0f, 0f, w, h * 0.42f, fallbackGlossPaint)

            if (isPressedState) {
                fallbackFillPaint.shader = null
                fallbackFillPaint.color = Color.argb(if (dark) 36 else 44, 255, 255, 255)
                drawRect(rect, fallbackFillPaint)
            }
        }

        // Dual-lobe 45° specular rim highlight
        val rimHi = Color.argb((spec.specular * (if (dark) 185 else 245)).toInt().coerceAtMost(255), 255, 255, 255)
        val rimMid = Color.argb((spec.specular * (if (dark) 95 else 140)).toInt().coerceAtMost(255), 255, 255, 255)
        val rimLow = Color.argb(if (dark) 26 else 38, 255, 255, 255)
        fallbackRimPaint.strokeWidth = 1.25f * density
        fallbackRimPaint.shader = SweepGradient(
            w * 0.5f,
            h * 0.5f,
            intArrayOf(rimLow, rimMid, rimLow, rimHi, rimMid, rimLow),
            floatArrayOf(0f, 0.14f, 0.36f, 0.62f, 0.80f, 1f),
        )
        val inset = 0.7f * density
        rect.set(inset, inset, w - inset, h - inset)
        canvas.drawRoundRect(rect, (radius - inset).coerceAtLeast(0f), (radius - inset).coerceAtLeast(0f), fallbackRimPaint)
    }

    private data class GlassOptics(
        val bezelDp: Float,
        val refractionDp: Float,
        val dispersion: Float,
        val blurDp: Float,
        val tintColor: Int,
        val specular: Float,
        val vibrancy: Float,
        val shadowAlpha: Float,
    )

    private fun opticsFor(preset: LiquidGlassPreset, dark: Boolean): GlassOptics = when (preset) {
        LiquidGlassPreset.PANEL -> if (dark) {
            GlassOptics(
                bezelDp = 18f,
                refractionDp = 22f,
                dispersion = 0.24f,
                blurDp = 3.2f,
                tintColor = Color.argb(26, 232, 246, 255), // ~10% ultra-clear dark glass
                specular = 0.82f,
                vibrancy = 1.22f,
                shadowAlpha = 0.14f,
            )
        } else {
            GlassOptics(
                bezelDp = 18f,
                refractionDp = 22f,
                dispersion = 0.22f,
                blurDp = 3.0f,
                tintColor = Color.argb(66, 255, 255, 255), // ~26% crystal clear light glass
                specular = 0.96f,
                vibrancy = 1.18f,
                shadowAlpha = 0.05f,
            )
        }

        LiquidGlassPreset.TRACK -> if (dark) {
            GlassOptics(
                bezelDp = 11f,
                refractionDp = 10f,
                dispersion = 0.10f,
                blurDp = 3.5f,
                tintColor = Color.argb(88, 10, 18, 26),
                specular = 0.45f,
                vibrancy = 1.05f,
                shadowAlpha = 0f,
            )
        } else {
            GlassOptics(
                bezelDp = 11f,
                refractionDp = 10f,
                dispersion = 0.10f,
                blurDp = 3.2f,
                tintColor = Color.argb(42, 28, 48, 68),
                specular = 0.65f,
                vibrancy = 1.06f,
                shadowAlpha = 0f,
            )
        }

        LiquidGlassPreset.THUMB -> if (dark) {
            GlassOptics(
                bezelDp = 14f,
                refractionDp = 30f,
                dispersion = 0.34f,
                blurDp = 1.4f,
                tintColor = Color.argb(46, 210, 252, 238), // subtle mint-crystal lens
                specular = 0.98f,
                vibrancy = 1.28f,
                shadowAlpha = 0.16f,
            )
        } else {
            GlassOptics(
                bezelDp = 14f,
                refractionDp = 30f,
                dispersion = 0.32f,
                blurDp = 1.2f,
                tintColor = Color.argb(96, 248, 255, 252), // luminous water-drop lens
                specular = 1.0f,
                vibrancy = 1.22f,
                shadowAlpha = 0.08f,
            )
        }

        LiquidGlassPreset.BUTTON_CLEAR -> if (dark) {
            GlassOptics(
                bezelDp = 12f,
                refractionDp = 24f,
                dispersion = 0.30f,
                blurDp = 1.8f,
                tintColor = Color.argb(36, 235, 248, 255),
                specular = 0.92f,
                vibrancy = 1.24f,
                shadowAlpha = 0.14f,
            )
        } else {
            GlassOptics(
                bezelDp = 12f,
                refractionDp = 24f,
                dispersion = 0.28f,
                blurDp = 1.6f,
                tintColor = Color.argb(78, 255, 255, 255),
                specular = 0.98f,
                vibrancy = 1.20f,
                shadowAlpha = 0.06f,
            )
        }

        LiquidGlassPreset.BUTTON_MINT -> if (dark) {
            GlassOptics(
                bezelDp = 11f,
                refractionDp = 20f,
                dispersion = 0.20f,
                blurDp = 1.8f,
                tintColor = Color.argb(122, 146, 238, 202), // translucent mint crystal
                specular = 0.94f,
                vibrancy = 1.18f,
                shadowAlpha = 0.14f,
            )
        } else {
            GlassOptics(
                bezelDp = 11f,
                refractionDp = 20f,
                dispersion = 0.20f,
                blurDp = 1.6f,
                tintColor = Color.argb(148, 134, 228, 191), // crisp translucent mint glass
                specular = 0.98f,
                vibrancy = 1.16f,
                shadowAlpha = 0.07f,
            )
        }

        LiquidGlassPreset.FIELD -> if (dark) {
            GlassOptics(
                bezelDp = 10f,
                refractionDp = 12f,
                dispersion = 0.12f,
                blurDp = 2.6f,
                tintColor = Color.argb(64, 10, 18, 26),
                specular = 0.50f,
                vibrancy = 1.08f,
                shadowAlpha = 0f,
            )
        } else {
            GlassOptics(
                bezelDp = 10f,
                refractionDp = 12f,
                dispersion = 0.12f,
                blurDp = 2.2f,
                tintColor = Color.argb(72, 255, 255, 255),
                specular = 0.74f,
                vibrancy = 1.08f,
                shadowAlpha = 0f,
            )
        }
    }

    private companion object {
        val LIQUID_GLASS_AGSL = """
            uniform shader backdrop;
            uniform float2 resolution;
            uniform float2 viewOrigin;
            uniform float cornerRadius;
            uniform float bezelWidth;
            uniform float refraction;
            uniform float dispersion;
            uniform float blurRadius;
            uniform half4 tintColor;
            uniform float specularStrength;
            uniform float vibrancy;
            uniform float darkMode;
            uniform float pressed;
            uniform float disabled;

            float sdRoundedBox(float2 p, float2 b, float r) {
                float2 q = abs(p) - b + float2(r);
                return min(max(q.x, q.y), 0.0) + length(max(q, float2(0.0))) - r;
            }

            half3 sampleBlurred(float2 pos, float rad) {
                half3 c = backdrop.eval(pos).rgb * 0.28;
                float r1 = rad;
                float r2 = rad * 0.7071;
                c += backdrop.eval(pos + float2(r1, 0.0)).rgb * 0.09;
                c += backdrop.eval(pos + float2(-r1, 0.0)).rgb * 0.09;
                c += backdrop.eval(pos + float2(0.0, r1)).rgb * 0.09;
                c += backdrop.eval(pos + float2(0.0, -r1)).rgb * 0.09;
                c += backdrop.eval(pos + float2(r2, r2)).rgb * 0.09;
                c += backdrop.eval(pos + float2(-r2, r2)).rgb * 0.09;
                c += backdrop.eval(pos + float2(r2, -r2)).rgb * 0.09;
                c += backdrop.eval(pos + float2(-r2, -r2)).rgb * 0.09;
                return c;
            }

            half4 main(float2 fragCoord) {
                float2 halfSize = resolution * 0.5;
                float2 p = fragCoord - halfSize;
                float r = min(cornerRadius, min(halfSize.x, halfSize.y));
                float sdf = sdRoundedBox(p, halfSize, r);

                // Anti-aliased shape alpha
                float shapeAlpha = clamp(0.5 - sdf, 0.0, 1.0);
                if (shapeAlpha <= 0.001) {
                    return half4(0.0);
                }

                // Outward-pointing edge normal via central difference
                float eps = 1.2;
                float2 grad = float2(
                    sdRoundedBox(p + float2(eps, 0.0), halfSize, r) - sdRoundedBox(p - float2(eps, 0.0), halfSize, r),
                    sdRoundedBox(p + float2(0.0, eps), halfSize, r) - sdRoundedBox(p - float2(0.0, eps), halfSize, r)
                );
                float2 normal = grad / max(length(grad), 0.0001);

                // Convex glass bezel curvature (steep near rim, crystal flat in center)
                float edgeDist = max(-sdf, 0.0);
                float bezelT = clamp(1.0 - edgeDist / max(bezelWidth, 1.0), 0.0, 1.0);
                float curve = bezelT * bezelT * (3.0 - 2.0 * bezelT);
                float pressBoost = 1.0 + pressed * 0.25;

                // Subtle center lens magnification + strong bezel edge bending
                float2 centerShift = (p / max(halfSize, float2(1.0))) * (-2.2 * pressBoost);
                float2 refractVec = -normal * (curve * refraction * pressBoost) + centerShift;
                float2 baseCoord = viewOrigin + fragCoord + refractVec;

                // Chromatic dispersion along the curved bezel rim
                float dispAmt = curve * refraction * dispersion * 0.22;
                float2 dispVec = normal * dispAmt;
                float effectiveBlur = blurRadius * (1.0 - curve * 0.45);

                half3 midSample = sampleBlurred(baseCoord, effectiveBlur);
                half rChan = backdrop.eval(baseCoord + dispVec).r;
                half bChan = backdrop.eval(baseCoord - dispVec).b;
                half3 refracted = half3(
                    mix(midSample.r, rChan, half(curve * 0.72)),
                    midSample.g,
                    mix(midSample.b, bChan, half(curve * 0.72))
                );

                // Optical vibrancy boost so glass feels liquid & luminous
                half luma = dot(refracted, half3(0.299, 0.587, 0.114));
                refracted = clamp(mix(half3(luma), refracted, half(vibrancy)), half3(0.0), half3(1.0));

                // Translucent glass body tint
                half tintAlpha = tintColor.a * half(1.0 - disabled * 0.35);
                half3 glassCol = mix(refracted, tintColor.rgb, tintAlpha);

                // Upper Fresnel sheen (subtle curved dome reflection)
                float uvY = fragCoord.y / max(resolution.y, 1.0);
                float domeSheen = pow(clamp(1.0 - uvY * 1.9, 0.0, 1.0), 2.2) * (darkMode > 0.5 ? 0.11 : 0.20);
                glassCol += half3(domeSheen);

                // Subtle dark internal thickness shadow along lower-right inner bezel
                float innerShade = smoothstep(bezelWidth * 0.9, 0.0, edgeDist) *
                    clamp(dot(normal, normalize(float2(0.65, 0.76))), 0.0, 1.0) *
                    (darkMode > 0.5 ? 0.12 : 0.06);
                glassCol -= half3(innerShade);

                // Dual-lobe 45-degree Apple Liquid Glass specular rim light
                float rimBand = smoothstep(3.0, 0.0, edgeDist);
                float fineRim = smoothstep(1.6, 0.0, edgeDist);
                float2 lightDir = normalize(float2(-0.72, -0.69));
                float nDotL = dot(normal, lightDir);
                float primaryArc = pow(max(nDotL, 0.0), 2.4);
                float counterArc = pow(max(-nDotL, 0.0), 3.2) * 0.62;
                float rimGlow = fineRim * (0.18 + primaryArc * 0.92 + counterArc * 0.75) +
                    rimBand * (primaryArc * 0.24 + counterArc * 0.14);
                rimGlow *= specularStrength * (1.0 + pressed * 0.30);

                // Delicate icy-cyan / warm-white prism tint on the specular rim
                half3 rimTint = mix(half3(1.0, 1.0, 1.0), half3(0.86, 0.98, 1.0), half(curve * 0.35));
                glassCol = clamp(glassCol + rimTint * half(rimGlow), half3(0.0), half3(1.0));

                // Pressed luminous bloom
                glassCol = clamp(glassCol + half3(pressed * (darkMode > 0.5 ? 0.08 : 0.10)), half3(0.0), half3(1.0));

                return half4(glassCol * half(shapeAlpha), half(shapeAlpha));
            }
        """.trimIndent()
    }
}

class LiquidGlassFrameLayout @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0,
) : FrameLayout(context, attrs, defStyleAttr), LiquidGlassHost {

    private val surface = LiquidGlassSurface(this)

    init {
        setWillNotDraw(false)
    }

    override fun bindBackdrop(backdrop: LiquidBackdropView) = surface.bindBackdrop(backdrop)
    override fun setGlassPreset(preset: LiquidGlassPreset) = surface.setPreset(preset)
    override fun setGlassCornerRadiusDp(radiusDp: Float) = surface.setCornerRadiusDp(radiusDp)
    override fun refreshGlass() = invalidate()

    override fun drawableStateChanged() {
        super.drawableStateChanged()
        surface.updateDrawableState(isPressed, isEnabled)
    }

    override fun onDraw(canvas: Canvas) {
        surface.draw(canvas)
        super.onDraw(canvas)
    }

    override fun onDetachedFromWindow() {
        surface.detachBackdrop()
        super.onDetachedFromWindow()
    }
}

class LiquidGlassLinearLayout @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0,
) : LinearLayout(context, attrs, defStyleAttr), LiquidGlassHost {

    private val surface = LiquidGlassSurface(this)

    init {
        setWillNotDraw(false)
    }

    override fun bindBackdrop(backdrop: LiquidBackdropView) = surface.bindBackdrop(backdrop)
    override fun setGlassPreset(preset: LiquidGlassPreset) = surface.setPreset(preset)
    override fun setGlassCornerRadiusDp(radiusDp: Float) = surface.setCornerRadiusDp(radiusDp)
    override fun refreshGlass() = invalidate()

    override fun drawableStateChanged() {
        super.drawableStateChanged()
        surface.updateDrawableState(isPressed, isEnabled)
    }

    override fun onDraw(canvas: Canvas) {
        surface.draw(canvas)
        super.onDraw(canvas)
    }

    override fun onDetachedFromWindow() {
        surface.detachBackdrop()
        super.onDetachedFromWindow()
    }
}

class LiquidGlassPillView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0,
) : View(context, attrs, defStyleAttr), LiquidGlassHost {

    private val surface = LiquidGlassSurface(this)

    override fun bindBackdrop(backdrop: LiquidBackdropView) = surface.bindBackdrop(backdrop)
    override fun setGlassPreset(preset: LiquidGlassPreset) = surface.setPreset(preset)
    override fun setGlassCornerRadiusDp(radiusDp: Float) = surface.setCornerRadiusDp(radiusDp)
    override fun refreshGlass() = invalidate()

    override fun drawableStateChanged() {
        super.drawableStateChanged()
        surface.updateDrawableState(isPressed, isEnabled)
    }

    override fun onDraw(canvas: Canvas) {
        surface.draw(canvas)
    }

    override fun onDetachedFromWindow() {
        surface.detachBackdrop()
        super.onDetachedFromWindow()
    }
}
