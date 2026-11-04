// Shared screen and control geometry; portable C for deterministic layout tests.
#pragma once
#include <math.h>
typedef struct { double x, y, w, h; } N3DSRect;
typedef struct { double x, y; } N3DSPoint;
typedef enum { N3DS_SPLIT, N3DS_TOP_ONLY, N3DS_BOTTOM_ONLY, N3DS_VERTICAL } N3DSDisplay;
typedef struct { N3DSRect top, bottom, controls; int landscape_options; double control_unit; int bottom_menu; } N3DSGeometry;
static inline N3DSRect n3ds_fit(N3DSRect b, double aspect) {
    double w = fmax(0, fmin(b.w, b.h * aspect)), h = w / aspect;
    return (N3DSRect){b.x + (b.w-w)/2, b.y + (b.h-h)/2, w, h};
}
static inline double n3ds_portrait_unit(N3DSRect safe) {
    double width = fmin(safe.w,safe.h), height = fmax(safe.w,safe.h);
    return 1.25 * fmin(fmin(fmin(290,height*.34)/430, width/700),1.3);
}
static inline double n3ds_control_radius(double u, int group) {
    switch (group) {
        case 0: return fmax(40,77*u);
        case 1: return fmax(27.5,48*u);
        case 2: return fmax(21.25,27*u);
        default: return fmax(27.5,60*u);
    }
}
static inline double n3ds_face_gap(double u) { return fmax(32.5,50*u); }
static inline double n3ds_face_hit(double u) { return fmax(22,n3ds_control_radius(u,2)*1.25); }
static inline double n3ds_control_grab(double u, int group) {
    if (group == 0) return fmax(22,n3ds_control_radius(u,0)*1.4);
    if (group == 1) return fmax(22,n3ds_control_radius(u,1)*1.3);
    if (group == 2) return n3ds_face_gap(u)+fmax(n3ds_control_radius(u,2)*1.6,n3ds_face_hit(u));
    return fmax(22,n3ds_control_radius(u,3));
}
static inline N3DSRect n3ds_control_body(N3DSRect b, int bottom_menu) {
    if (bottom_menu) b.h = fmax(0,b.h-64);
    return b;
}
static inline N3DSPoint n3ds_control_anchor(N3DSRect b, int group) {
    double x[] = {.14, .37, .86, .63};
    double y[] = {.48, .77, .48, .82};
    return (N3DSPoint){b.x+b.w*x[group], b.y+44+fmax(0,b.h-44)*y[group]};
}
static inline N3DSPoint n3ds_control_position(N3DSRect controls, int group, double u, int bottom_menu) {
    if (bottom_menu && controls.w >= 600) {
        // The lower row shares the menu's height, using its empty outer flanks.
        if (group == 1 || group == 3) return (N3DSPoint){controls.x+controls.w*(group == 1 ? .24 : .76),
            controls.y+controls.h-n3ds_control_grab(u,group)-8};
        double edge = group == 0 ? n3ds_control_radius(u,0)+8 : n3ds_face_gap(u)+n3ds_control_radius(u,2)+8;
        return (N3DSPoint){group == 0 ? controls.x+edge : controls.x+controls.w-edge,
            controls.y+controls.h*.58};
    }
    N3DSRect b = n3ds_control_body(controls,bottom_menu);
    double margin = group == 2 ? n3ds_face_gap(u)+n3ds_face_hit(u) : n3ds_control_radius(u,group)+6;
    N3DSPoint p = n3ds_control_anchor(b,group);
    p.x = fmin(fmax(p.x,b.x+margin),b.x+b.w-margin);
    p.y = fmin(fmax(p.y,b.y+44+margin),b.y+b.h-margin);
    return p;
}
static inline N3DSRect n3ds_small_display(N3DSRect lower, double u, double aspect) {
    // Reserve visible control footprints, not the oversized invisible grab circles.
    double left = 2*n3ds_control_radius(u,0)+16;
    double right = 2*(n3ds_face_gap(u)+n3ds_control_radius(u,2))+16;
    double row = 2*fmax(n3ds_control_grab(u,1),n3ds_control_grab(u,3))+16;
    N3DSRect canvas = {lower.x+left,lower.y+8,fmax(0,lower.w-left-right),fmax(0,lower.h-row)};
    N3DSRect screen = n3ds_fit(canvas,aspect);
    screen.y = canvas.y;
    return screen;
}
static inline N3DSGeometry n3ds_geometry(N3DSRect safe, N3DSRect division, int partially_open, N3DSDisplay display, int flip) {
    N3DSGeometry result = {0};
    if (safe.w <= 0 || safe.h <= 0) return result;
    result.control_unit = n3ds_portrait_unit(safe);
    double required = 44+2*fmax(n3ds_control_radius(result.control_unit,0)+6,n3ds_face_gap(result.control_unit)+n3ds_face_hit(result.control_unit));
    int portrait = safe.h >= safe.w;
    int horizontal_fold = (partially_open || portrait) && division.w >= safe.w*.9 && division.w > division.h &&
        division.y > safe.y && division.y+division.h < safe.y+safe.h;
    result.landscape_options = !portrait && !partially_open;
    if (partially_open) display = N3DS_SPLIT;
    if (portrait && safe.w < 600 && !horizontal_fold) {
        // A phone-width window cannot put a readable display between large controls.
        // Give both screens dedicated rows and place the complete pad below them.
        double band = required+2*fmax(n3ds_control_radius(result.control_unit,1),n3ds_control_radius(result.control_unit,3))+80;
        band = fmin(band,safe.h*.50);
        result.controls = (N3DSRect){safe.x,safe.y+safe.h-band,safe.w,band};
        result.bottom_menu = 1;
        double scale = fmax(0,fmin(safe.w/400.,(safe.h-band-16)/480.));
        double y = safe.y+fmax(0,(safe.h-band-8-480*scale)/2);
        result.top = (N3DSRect){safe.x+(safe.w-400*scale)/2,y,400*scale,240*scale};
        result.bottom = (N3DSRect){safe.x+(safe.w-320*scale)/2,y+240*scale+8,320*scale,240*scale};
        return result;
    }
    if (portrait || horizontal_fold || display == N3DS_VERTICAL) {
        // Flat portrait uses the same upper/lower panel arrangement as a bent Duo.
        double lower_height = fmax(safe.h*.5,fmin(safe.h*.78,required+80));
        double cut = horizontal_fold ? division.y : safe.y+safe.h-lower_height;
        double gap = horizontal_fold ? division.h : 0;
        N3DSRect upper = {safe.x,safe.y,safe.w,fmax(0,cut-safe.y-8)};
        N3DSRect lower = {safe.x,cut+gap+8,safe.w,fmax(0,safe.y+safe.h-cut-gap-8)};
        result.controls = lower; result.bottom_menu = 1;
        int bottom_large = !portrait && !horizontal_fold && display == N3DS_VERTICAL && flip;
        if (bottom_large) {
            result.bottom = n3ds_fit(upper,320./240.);
            result.top = n3ds_small_display(lower,result.control_unit,400./240.);
        } else {
            result.top = n3ds_fit(upper,400./240.);
            result.bottom = n3ds_small_display(lower,result.control_unit,320./240.);
        }
        return result;
    }
    double band = fmin(safe.h*.60,fmax(required,fmin(200,safe.h*.43)));
    N3DSRect canvas = safe; canvas.h = fmax(0,safe.h-band-8);
    result.controls = (N3DSRect){safe.x,safe.y+canvas.h+8,safe.w,band};
    if (display == N3DS_TOP_ONLY) result.top = n3ds_fit(canvas,400./240.);
    else if (display == N3DS_BOTTOM_ONLY) result.bottom = n3ds_fit(canvas,320./240.);
    else {
        double width = fmax(0,canvas.w-8)/2;
        result.top = n3ds_fit((N3DSRect){canvas.x,canvas.y,width,canvas.h},400./240.);
        result.bottom = n3ds_fit((N3DSRect){canvas.x+width+8,canvas.y,width,canvas.h},320./240.);
    }
    return result;
}
