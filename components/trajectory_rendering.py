"""Pure helpers for rendering sampled trajectory segments."""


def trajectory_segments(points):
    """Group adjacent points by segment without inventing intermediate points."""
    segments = []
    current = []
    previous_segment_id = None
    for point in points:
        if current and point.segment_id != previous_segment_id:
            segments.append(tuple(current))
            current = []
        current.append(point)
        previous_segment_id = point.segment_id
    if current:
        segments.append(tuple(current))
    return tuple(segments)
