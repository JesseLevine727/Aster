#include "../common/bench_record.h"
#include <iostream>
#include <string>

int main() {
    std::string length;
    while (std::getline(std::cin, length)) {
        std::string line(std::stoul(length), '\0');
        std::cin.read(line.data(), line.size());
        try { validate_bench_record(line); std::cout << "PASS\n"; }
        catch (const std::exception&) { std::cout << "FAIL\n"; }
    }
}
