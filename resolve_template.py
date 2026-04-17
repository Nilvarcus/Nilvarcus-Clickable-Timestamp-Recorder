import sys

# ==========================================
# CONFIGURATION - CHANGE THESE IF NEEDED
# ==========================================
FPS = 60                   # Match this to your video's frame rate
CLIP_DURATION_SECONDS = 6  # How many seconds each cut should last
# ==========================================

resolve = app.GetResolve()
projectManager = resolve.GetProjectManager()
project = projectManager.GetCurrentProject()
mediaPool = project.GetMediaPool()

# FORMAT: ("Clip Name in Media Pool", "Timestamp", "Note")
timestamps =[
    # TIMESTAMPS_PLACEHOLDER
]

def tc_to_frames(tc_str, fps):
    # Converts Timecode to Frame integer
    parts = str(tc_str).replace(';', ':').split(':')
    if len(parts) == 4:
        h, m, s, f = map(int, parts)
        return int((h * 3600 + m * 60 + s) * fps + f)
    elif len(parts) == 3:
        h, m, s = map(int, parts)
        return int((h * 3600 + m * 60 + s) * fps)
    return 0

# 1. Grab all clips in the current bin and map them by their name
folder = mediaPool.GetCurrentFolder()
clips = folder.GetClipList()

if not clips:
    print("ERROR: No clips found! Make sure you are in a bin with your source videos.")
    sys.exit()

# Create a dictionary so we can quickly look up a clip by its name
clip_dictionary = {clip.GetName(): clip for clip in clips}

# 2. Prepare the cut list
append_list = []
valid_timestamps =[] # Keep track of which ones actually found a matching file

for filename, time_str, desc in timestamps:
    if filename not in clip_dictionary:
        print(f"WARNING: Could not find '{filename}' in the current bin. Skipping this timestamp: {time_str}")
        continue
        
    source_clip = clip_dictionary[filename]
    
    # Calculate starting frame offset for THIS specific file
    start_tc = source_clip.GetClipProperty("Start TC")
    base_frames = tc_to_frames(start_tc, FPS)
    
    target_f = tc_to_frames(time_str, FPS)
    start_f = base_frames + target_f
    end_f = start_f + (CLIP_DURATION_SECONDS * FPS)
    
    append_list.append({
        "mediaPoolItem": source_clip,
        "startFrame": start_f,
        "endFrame": end_f
    })
    
    # Save to our valid list so we can apply markers perfectly later
    valid_timestamps.append((filename, time_str, desc, base_frames))

# 3. Create a fresh Timeline and append our cuts
timeline = mediaPool.CreateEmptyTimeline("TIMELINE_NAME_PLACEHOLDER")
if not append_list:
    print("ERROR: No valid clips were matched. Check your spelling of the filenames!")
    sys.exit()
    
mediaPool.AppendToTimeline(append_list)
print(f"Success! Built string-out timeline with {len(append_list)} clips.")

# 4. Add Context Markers directly to the CLIPS (Timeline Items)
current_timeline = project.GetCurrentTimeline()

# Grab all the newly generated clips on Video Track 1
video_items = current_timeline.GetItemListInTrack("video", 1)

# Zip our physical timeline clips together with our valid timestamp data
for clip_item, (filename, time_str, desc, base_frames) in zip(video_items, valid_timestamps):
    
    # Calculate exact source frame this clip starts on
    target_f = tc_to_frames(time_str, FPS)
    start_f = base_frames + target_f
    
    # Smart Color Coding System
    color = "Yellow"
    desc_upper = desc.upper()
    if "FAIL" in desc_upper or "TRAGEDY" in desc_upper or "REALITY CHECK" in desc_upper:
        color = "Red"
    elif "LEVEL" in desc_upper or "QUEST" in desc_upper:
        color = "Green"
    elif "BOSS" in desc_upper or "GUN MAN" in desc_upper or "BIG FROG" in desc_upper:
        color = "Fuchsia"
        
    # Add marker to the clip itself
    clip_item.AddMarker(start_f, color, "Log Note", desc, 1, "")
    
print("Added color-coded metadata markers directly to the clips!")
