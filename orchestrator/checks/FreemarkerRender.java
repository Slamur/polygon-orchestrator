// Раскрытие FreeMarker test-script'а настоящим FreeMarker — для
// orchestrator/checks/test_script_check.py. Запускается без сборки:
// `java -cp freemarker.jar FreemarkerRender.java <script>`; результат — в
// stdout, ошибка FreeMarker — в stderr с кодом 2.

import freemarker.template.Configuration;
import freemarker.template.Template;
import freemarker.template.TemplateExceptionHandler;

import java.io.OutputStreamWriter;
import java.io.Reader;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashMap;

public class FreemarkerRender {
    public static void main(String[] args) throws Exception {
        Path path = Path.of(args[0]);
        Configuration cfg = new Configuration(Configuration.VERSION_2_3_0);
        cfg.setDefaultEncoding("UTF-8");
        // Числа без разделителей групп разрядов (300000, а не 300,000) — как в
        // скрипте, раскрытом Polygon.
        cfg.setNumberFormat("computer");
        cfg.setTemplateExceptionHandler(TemplateExceptionHandler.RETHROW_HANDLER);
        cfg.setLogTemplateExceptions(false);
        Writer out = new OutputStreamWriter(System.out, StandardCharsets.UTF_8);
        try (Reader reader = Files.newBufferedReader(path, StandardCharsets.UTF_8)) {
            Template template = new Template(path.getFileName().toString(), reader, cfg);
            template.process(new HashMap<String, Object>(), out);
        } catch (Exception e) {
            out.flush();
            System.err.println(e.getMessage());
            System.exit(2);
        }
        out.flush();
    }
}
