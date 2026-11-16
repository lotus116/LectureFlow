package local.lectureflow.validation;

import androidx.test.platform.app.InstrumentationRegistry;
import org.junit.Test;
import org.junit.Before;
import org.junit.After;
import static org.junit.Assert.*;
import java.io.*;
import java.util.*;

public class CaptionProcessorTest {
    private File folder;
    private LectureStore store;
    @Before public void setUp() throws Exception {
        folder = new File(InstrumentationRegistry.getInstrumentation().getTargetContext().getCacheDir(), "processor-" + UUID.randomUUID());
        store = new LectureStore(folder);
    }
    @After public void tearDown() throws Exception {
        if (store != null) store.close();
        if (folder != null && folder.listFiles() != null) for (File f : folder.listFiles()) f.delete();
        if (folder != null) folder.delete();
    }
    private void audio(int seconds, long... boundaries) throws Exception {
        try (FileOutputStream f = new FileOutputStream(new File(folder, "recording.pcm"))) {
            for (int i = 0; i < seconds * 16000; i++) { f.write(0); f.write(16); }
        }
        try (DataOutputStream f = new DataOutputStream(new FileOutputStream(new File(folder, "boundaries.bin")))) {
            for (long boundary : boundaries) f.writeLong(boundary);
        }
    }
    private CaptionProcessor.State state(final long available) {
        return new CaptionProcessor.State() {
            public long available() { return available; }
            public boolean done() { return true; }
            public void progress(long consumed, String detail) {
                assertTrue(consumed <= available);
                if (detail.contains("中文") && consumed < available) observedBacklog = true;
            }
        };
    }
    private boolean observedBacklog;
    @Test public void testTwoPreviousCaptionsRevisedAndBacklogAccurate() throws Exception {
        audio(6, 32000, 64000);
        List<String> followingInputs = new ArrayList<>();
        CaptionProcessor.Inference model = new CaptionProcessor.Inference() {
            int sentence;
            public String recognize(float[] samples) { if (samples.length == 16000) sentence++; return "Sentence " + sentence; }
            public String translate(String text, String preceding, String following) {
                if (!following.isEmpty()) followingInputs.add(text + " <- " + following);
                return "中文 " + text;
            }
        };
        new CaptionProcessor(store, model, state(96000)).run();
        List<LectureStore.Caption> captions = store.recent(10);
        assertEquals(3, captions.size());
        assertEquals(2, captions.get(0).revisions);
        assertEquals(1, captions.get(1).revisions);
        assertEquals(0, captions.get(2).revisions);
        assertEquals(3, followingInputs.size());
        assertTrue(followingInputs.get(1).contains("\n"));
        assertTrue(observedBacklog);
        assertEquals(0, store.failures());
    }
    @Test public void testFailedRevisionPreservesOldChineseAndContinues() throws Exception {
        audio(4, 32000);
        CaptionProcessor.Inference model = new CaptionProcessor.Inference() {
            public String recognize(float[] samples) { return "Original speech"; }
            public String translate(String text, String preceding, String following) {
                if (!following.isEmpty()) throw new IllegalStateException("known failure");
                return "原有译文";
            }
        };
        new CaptionProcessor(store, model, state(64000)).run();
        List<LectureStore.Caption> captions = store.recent(10);
        assertEquals(2, captions.size());
        assertEquals("原有译文", captions.get(0).chinese);
        assertFalse(captions.get(0).error.isEmpty());
        assertEquals("原有译文", captions.get(1).chinese);
        assertEquals(1, store.failures());
        assertEquals(64000, store.resumeSample());
    }
    @Test public void testEmptyFinalAsrRetainsPartialAndRevisesPredecessor() throws Exception {
        audio(4, 32000);
        CaptionProcessor.Inference model = new CaptionProcessor.Inference() {
            public String recognize(float[] samples) { return samples.length >= 32000 ? "" : "Partial speech"; }
            public String translate(String text, String preceding, String following) { return "保存的中文"; }
        };
        new CaptionProcessor(store, model, state(64000)).run();
        List<LectureStore.Caption> captions = store.recent(10);
        assertEquals(2, captions.size());
        assertEquals("Partial speech", captions.get(1).english);
        assertTrue(captions.get(1).stable);
        assertEquals(1, captions.get(0).revisions);
    }
    @Test public void testAsrFailureLeavesOriginalAudioAndAudit() throws Exception {
        audio(2);
        CaptionProcessor.Inference model = new CaptionProcessor.Inference() {
            public String recognize(float[] samples) { throw new IllegalStateException("ASR failure"); }
            public String translate(String text, String preceding, String following) { fail("No translation expected"); return ""; }
        };
        try { new CaptionProcessor(store, model, state(32000)).run(); fail("Must report failure"); }
        catch (IllegalStateException expected) { assertEquals("ASR failure", expected.getMessage()); }
        assertEquals(64000, new File(folder, "recording.pcm").length());
        assertTrue(new File(folder, "events.jsonl").length() > 0);
    }
    @Test public void testPauseAtNonAlignedBoundaryFinalizesWhileCapturePaused() throws Exception {
        audio(1, 12345);
        java.util.concurrent.atomic.AtomicBoolean done = new java.util.concurrent.atomic.AtomicBoolean(false);
        java.util.concurrent.atomic.AtomicReference<Throwable> failure = new java.util.concurrent.atomic.AtomicReference<>();
        CaptionProcessor.Inference model = new CaptionProcessor.Inference() {
            public String recognize(float[] samples) { return "Pause boundary"; }
            public String translate(String text, String preceding, String following) { return "暂停处"; }
        };
        CaptionProcessor.State state = new CaptionProcessor.State() {
            public long available() { return 12345; }
            public boolean done() { return done.get(); }
            public void progress(long consumed, String detail) {}
        };
        Thread worker = new Thread(() -> {
            try { new CaptionProcessor(store, model, state).run(); }
            catch (Throwable error) { failure.set(error); }
        });
        worker.start();
        try {
            long deadline = System.currentTimeMillis() + 3000;
            while (store.resumeSample() != 12345 && System.currentTimeMillis() < deadline) Thread.sleep(20);
            assertEquals(12345, store.resumeSample());
        } finally {
            done.set(true);
            worker.join(2000);
            if (worker.isAlive()) { worker.interrupt(); worker.join(2000); }
        }
        assertNull(failure.get());
    }

    @Test public void testSilenceNeverCallsModels() throws Exception {
        audio(6);
        try (RandomAccessFile file = new RandomAccessFile(new File(folder, "recording.pcm"), "rw")) {
            file.write(new byte[6 * 32000]);
        }
        CaptionProcessor.Inference model = new CaptionProcessor.Inference() {
            public String recognize(float[] samples) { fail("Silence must not reach ASR"); return ""; }
            public String translate(String text, String preceding, String following) { fail("Silence must not reach translation"); return ""; }
        };
        new CaptionProcessor(store, model, state(96000)).run();
        assertTrue(store.recent(10).isEmpty());
        assertEquals(96000, store.resumeSample());
    }
    @Test public void testSpeechAfterSilenceIsRecognized() throws Exception {
        audio(6);
        try (RandomAccessFile file = new RandomAccessFile(new File(folder, "recording.pcm"), "rw")) {
            file.write(new byte[4 * 32000]);
        }
        CaptionProcessor.Inference model = new CaptionProcessor.Inference() {
            public String recognize(float[] samples) { return "Speech after quiet"; }
            public String translate(String text, String preceding, String following) { return "静音后的讲话"; }
        };
        new CaptionProcessor(store, model, state(96000)).run();
        List<LectureStore.Caption> captions = store.recent(10);
        assertEquals(1, captions.size());
        assertEquals("静音后的讲话", captions.get(0).chinese);
        assertTrue(captions.get(0).start >= 64000);
    }

}
