#include "../common/parallel_record.h"
#include <iostream>
int main() {
    std::size_t length;
    while (std::cin >> length) {
        std::cin.get();
        std::string line(length, '\0');
        std::cin.read(line.data(), length);
        try { validate_parallel_record(line); std::cout << "PASS\n"; }
        catch (const std::exception&) { std::cout << "FAIL\n"; }
    }
}
