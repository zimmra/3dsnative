// Shared screen and control geometry; portable C for deterministic layout tests.
#pragma once
#include <math.h>
typedef struct { double x, y, w, h; } N3DSRect;
typedef struct { double x, y; } N3DSPoint;
typedef enum { N3DS_SPLIT, N3DS_TOP_ONLY, N3DS_BOTTOM_ONLY, N3DS_VERTICAL } N3DSDisplay;
typedef struct { N3DSRect top, bottom, controls; int landscape_options; } N3DSGeometry;
static inline N3DSRect n3ds_fit(N3DSRect b, double aspect) {
    double w = fmax(0, fmin(b.w, b.h * aspect)), h = w / aspect;
    return (N3DSRect){b.x + (b.w-w)/2, b.y + (b.h-h)/2, w, h};
}
static inline N3DSGeometry n3ds_geometry(N3DSRect safe, N3DSRect division, int partially_open, N3DSDisplay display, int flip) {
    N3DSGeometry result = {0};
    if (safe.w <= 0 || safe.h <= 0) return result;
    int horizontal_fold = partially_open && division.w >= safe.w * .9 && division.w > division.h &&
        division.y > safe.y && division.y + division.h < safe.y + safe.h;
    int portrait = safe.h >= safe.w;
    N3DSRect canvas = safe;
    if (horizontal_fold) {
        N3DSRect upper = {safe.x, safe.y, safe.w, division.y-safe.y-8};
        N3DSRect lower = {safe.x, division.y+division.h+8, safe.w, safe.y+safe.h-division.y-division.h-8};
        result.top = n3ds_fit(upper, 400./240.);
        double band = fmin(lower.h*.50, 290);
        canvas = lower; canvas.h = fmax(0, lower.h-band-8);
        result.bottom = n3ds_fit(canvas, 320./240.);
        result.controls = (N3DSRect){lower.x, lower.y+canvas.h+8, lower.w, band};
        return result;
    }
    double band = fmin(portrait ? 290 : 200, safe.h * (portrait ? .34 : .43));
    // Keep the enlarged face buttons below the shoulder row on short displays.
    if (!portrait) band = fmin(safe.h * .60, fmax(170, band));
    canvas.h = fmax(0, safe.h-band-8);
    result.controls = (N3DSRect){safe.x, safe.y+canvas.h+8, safe.w, band};
    result.landscape_options = !portrait && !partially_open;
    if (partially_open) display = N3DS_SPLIT;
    if (portrait || display == N3DS_VERTICAL) {
        double fraction = portrait ? .45 : .70;
        if (flip && !portrait) fraction = 1-fraction;
        N3DSRect a = {canvas.x, canvas.y, canvas.w, fmax(0, canvas.h-8)*fraction};
        N3DSRect b = {canvas.x, canvas.y+a.h+8, canvas.w, fmax(0, canvas.h-a.h-8)};
        result.top = n3ds_fit(a, 400./240.); result.bottom = n3ds_fit(b, 320./240.);
    } else if (display == N3DS_TOP_ONLY) result.top = n3ds_fit(canvas, 400./240.);
    else if (display == N3DS_BOTTOM_ONLY) result.bottom = n3ds_fit(canvas, 320./240.);
    else {
        double width = fmax(0, canvas.w-8)/2;
        result.top = n3ds_fit((N3DSRect){canvas.x,canvas.y,width,canvas.h}, 400./240.);
        result.bottom = n3ds_fit((N3DSRect){canvas.x+width+8,canvas.y,width,canvas.h}, 320./240.);
    }
    return result;
}
// Separate four control groups instead of nesting a stick inside another group.
static inline N3DSPoint n3ds_control_anchor(N3DSRect b, int group) {
    double x[] = {.14, .37, .86, .63};
    double y[] = {.48, .77, .48, .82};
    return (N3DSPoint){b.x+b.w*x[group], b.y+44+fmax(0,b.h-44)*y[group]};
}
