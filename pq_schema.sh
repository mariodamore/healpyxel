pq_schema() {
    if [ -z "$1" ]; then
        echo "Usage: pq_schema <path_to_parquet_file>"
        return 1
    fi

    duckdb -c "DESCRIBE SELECT * FROM '$1';"
}
