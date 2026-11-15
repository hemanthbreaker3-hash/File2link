import unittest
from app.streamer.probe import _parse_probe_data


class ProbeParsingTests(unittest.TestCase):
    def test_detects_multiple_audio_tracks_and_labels(self):
        data = {"streams": [
            {"index": 0, "codec_type": "video", "codec_name": "h264",
             "width": 1920, "height": 1080, "r_frame_rate": "24000/1001"},
            {"index": 1, "codec_type": "audio", "codec_name": "aac",
             "channels": 2, "tags": {"language": "eng"},
             "disposition": {"default": 1}},
            {"index": 2, "codec_type": "audio", "codec_name": "ac3",
             "channels": 6, "tags": {"language": "hin", "title": "Dub"},
             "disposition": {"default": 0}},
            {"index": 3, "codec_type": "subtitle", "codec_name": "subrip",
             "tags": {"language": "eng"}},
        ]}
        result = _parse_probe_data(data)
        self.assertTrue(result["has_multiple_audio"])
        self.assertEqual([x["audio_index"] for x in result["audio_tracks"]], [0, 1])
        self.assertEqual(result["audio_tracks"][0]["label"], "English (AAC Stereo)")
        self.assertIn("Hindi", result["audio_tracks"][1]["label"])
        self.assertEqual(result["audio_tracks"][1]["channels"], 6)
        self.assertEqual(len(result["subtitle_tracks"]), 1)
        self.assertEqual(result["probe_version"], 2)

    def test_video_without_audio(self):
        result = _parse_probe_data({"streams": [
            {"index": 0, "codec_type": "video", "codec_name": "h264",
             "r_frame_rate": "25/1"}
        ]})
        self.assertFalse(result["has_multiple_audio"])
        self.assertEqual(result["audio_tracks"], [])
        self.assertEqual(len(result["video_tracks"]), 1)


if __name__ == "__main__":
    unittest.main()
