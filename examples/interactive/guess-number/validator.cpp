#include "problem_lib.h"

// Границы из секции validator.constants файла constraints.yaml.
const int minN = 1, maxN = 1000;
const int minX = 1, maxX = 1000;   // фактическая верхняя граница X — прочитанное N
const int minQ = 0, maxQ = 10;     // константа условия: лимит запросов, во входном файле отсутствует

int main(int argc, char* argv[]) {
    registerValidation(argc, argv);

    // Первая строка: N.
    int n = inf.readInt(minN, maxN, "N");
    inf.readEoln();

    // Вторая строка: X, 1 <= X <= N.
    inf.readInt(minX, std::min(maxX, n), "X");
    inf.readEoln();

    inf.readEof();

    return 0;
}
