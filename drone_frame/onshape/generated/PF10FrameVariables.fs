FeatureScript 2399;
import(path : "onshape/std/common.fs", version : "2399.0");

// PF10 Frame Variables - GENERATED 2026-11-05 by frame_pipeline/onshape_export.py.
// Insert this feature first in a Part Studio; every #pf10_* variable is then available to later features.

annotation { "Feature Type Name" : "PF10 Frame Variables" }
export const pf10FrameVariables = defineFeature(function(context is Context, id is Id, definition is map)
    precondition
    {
    }
    {
        setVariable(context, "pf10_arm_angle", 49.9 * degree); // arm angle from +X (forward)
        setVariable(context, "pf10_motor_radius", 212.72 * millimeter); // body centre to motor centre
        setVariable(context, "pf10_root_radius", 68.03 * millimeter); // body centre to arm root face
        setVariable(context, "pf10_arm_L", 144.69 * millimeter); // free arm length root face -> motor centre
        setVariable(context, "pf10_arm_b", 21.862 * millimeter); // arm width
        setVariable(context, "pf10_arm_hr", 33.994 * millimeter); // arm root height
        setVariable(context, "pf10_arm_ht", 13.626 * millimeter); // arm tip height
        setVariable(context, "pf10_arm_tw", 1.86 * millimeter); // web thickness = 3 wall loops
        setVariable(context, "pf10_arm_tf", 1.44 * millimeter); // flange thickness = 6 skin layers
        setVariable(context, "pf10_tenon_L", 30 * millimeter); // tenon length
        setVariable(context, "pf10_tenon_b", 34.862 * millimeter); // tenon width incl. bolt ears
        setVariable(context, "pf10_retention_y", 14.181 * millimeter); // retention insert/screw lateral offset
        setVariable(context, "pf10_dv_depth", 6 * millimeter); // dovetail foot depth
        setVariable(context, "pf10_dv_angle", 30 * degree); // dovetail flank angle from vertical (60 deg dovetail)
        setVariable(context, "pf10_dv_flare", 3.464 * millimeter); // dovetail flare per side
        setVariable(context, "pf10_dv_home_gap", 3 * millimeter); // free travel behind the seated tenon
        setVariable(context, "pf10_dv_side_clear", 0.3 * millimeter); // straight-side clearance (no contact)
        setVariable(context, "pf10_dv_top_relief", 0.3 * millimeter); // top-plate relief over the tenon
        setVariable(context, "pf10_tie_x", -7.5 * millimeter); // tie bolt position along the arm (arm-local)
        setVariable(context, "pf10_tie_y", 23.895 * millimeter); // tie bolt lateral offset
        setVariable(context, "pf10_pad_d", 36 * millimeter); // motor pad diameter
        setVariable(context, "pf10_body_hx", 53 * millimeter); // body half length (X)
        setVariable(context, "pf10_body_hy", 43 * millimeter); // body half width (Y)
        setVariable(context, "pf10_H_int", 33.994 * millimeter); // body interior height
        setVariable(context, "pf10_t_bp", 3.2 * millimeter); // bottom plate thickness
        setVariable(context, "pf10_t_tp", 3 * millimeter); // top plate thickness
        setVariable(context, "pf10_wall_t", 2 * millimeter); // body wall thickness
        setVariable(context, "pf10_z_arm_top", 37.194 * millimeter); // arm top / top-plate underside Z
        setVariable(context, "pf10_socket_half_w", 26.895 * millimeter); // socket boss half width
        setVariable(context, "pf10_socket_wall", 6 * millimeter); // socket wall / back wall
        setVariable(context, "pf10_stack_pattern", 30.5 * millimeter); // FC/ESC stack hole pattern
        setVariable(context, "pf10_jetson_L", 100 * millimeter); // Jetson envelope L
        setVariable(context, "pf10_jetson_W", 80 * millimeter); // Jetson envelope W
        setVariable(context, "pf10_jetson_H", 36 * millimeter); // Jetson envelope H
        setVariable(context, "pf10_jetson_mx", 86 * millimeter); // carrier hole rectangle X (PLACEHOLDER)
        setVariable(context, "pf10_jetson_my", 58 * millimeter); // carrier hole rectangle Y (PLACEHOLDER)
        setVariable(context, "pf10_grommet_h", 6 * millimeter); // Jetson tray soft-mount gap
        setVariable(context, "pf10_prop_d", 254 * millimeter); // prop diameter
    });
