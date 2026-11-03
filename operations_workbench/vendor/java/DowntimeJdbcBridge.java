import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.ResultSetMetaData;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.Base64;
import java.util.List;
import java.util.Properties;

/**
 * Minimal subprocess bridge for a legacy Oracle database.
 *
 * SQL is supplied only by the Python service; browser input is validated and
 * passed as bind values. Database user and password are read from stdin (base64).
 */
public final class DowntimeJdbcBridge {
    private static String requiredEnv(String name) {
        String value = System.getenv(name);
        if (value == null || value.isBlank()) {
            throw new IllegalStateException("Missing required database configuration");
        }
        return value;
    }

    private static String decode(String value) {
        return new String(Base64.getDecoder().decode(value), StandardCharsets.UTF_8);
    }

    private static String encode(String value) {
        return Base64.getEncoder().encodeToString(value.getBytes(StandardCharsets.UTF_8));
    }

    public static void main(String[] args) {
        try {
            run(args);
        } catch (Exception error) {
            System.err.println(error.getMessage() != null ? error.getMessage() : error.toString());
            System.exit(1);
        }
    }

    private static void run(String[] args) throws Exception {
        BufferedReader input = new BufferedReader(
            new InputStreamReader(System.in, StandardCharsets.UTF_8)
        );
        String sql = decode(input.readLine());
        int parameterCount = Integer.parseInt(input.readLine());
        List<String> parameters = new ArrayList<>();
        for (int i = 0; i < parameterCount; i++) {
            parameters.add(decode(input.readLine()));
        }
        String userLine = input.readLine();
        String passwordLine = input.readLine();
        String user = userLine != null && !userLine.isBlank()
            ? decode(userLine)
            : requiredEnv("DOWNTIME_DB_USER");
        String password = passwordLine != null && !passwordLine.isBlank()
            ? decode(passwordLine)
            : requiredEnv("DOWNTIME_DB_PASSWORD");

        String url = buildJdbcUrl();
        Properties properties = new Properties();
        properties.setProperty("user", user);
        properties.setProperty("password", password);
        properties.setProperty("oracle.net.CONNECT_TIMEOUT", "10000");
        properties.setProperty("oracle.jdbc.ReadTimeout", "60000");

        Class.forName("oracle.jdbc.OracleDriver");
        try (Connection connection = DriverManager.getConnection(url, properties)) {
            connection.setReadOnly(true);
            try (Statement transaction = connection.createStatement()) {
                transaction.execute("SET TRANSACTION READ ONLY");
            } catch (Exception ignored) {
                // Some reporting accounts cannot start explicit read-only transactions.
            }
            try (PreparedStatement statement = connection.prepareStatement(sql)) {
                for (int i = 0; i < parameters.size(); i++) {
                    statement.setString(i + 1, parameters.get(i));
                }
                try (ResultSet results = statement.executeQuery()) {
                    ResultSetMetaData metadata = results.getMetaData();
                    int columns = metadata.getColumnCount();
                    List<String> output = new ArrayList<>();
                    List<String> header = new ArrayList<>();
                    for (int i = 1; i <= columns; i++) {
                        header.add(encode(metadata.getColumnLabel(i)));
                    }
                    output.add(String.join("\t", header));
                    while (results.next()) {
                        List<String> row = new ArrayList<>();
                        for (int i = 1; i <= columns; i++) {
                            String value = results.getString(i);
                            row.add(value == null ? "~" : encode(value));
                        }
                        output.add(String.join("\t", row));
                    }
                    System.out.print(String.join("\n", output));
                }
            }
        }
    }

    private static String buildJdbcUrl() {
        String alias = System.getenv("DOWNTIME_TNS_NAME");
        if (alias != null && !alias.isBlank()) {
            return "jdbc:oracle:thin:@" + alias.trim();
        }
        String tns = System.getenv("DOWNTIME_DB_TNS");
        if (tns != null && !tns.isBlank()) {
            return "jdbc:oracle:thin:@" + tns.trim();
        }
        return String.format(
            "jdbc:oracle:thin:@//%s:%s/%s",
            requiredEnv("DOWNTIME_DB_HOST"),
            requiredEnv("DOWNTIME_DB_PORT"),
            requiredEnv("DOWNTIME_DB_SERVICE")
        );
    }
}
