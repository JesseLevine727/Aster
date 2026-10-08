#include "../common/asterbench_v12_record.h"

#include <iostream>
#include <string>

// Length-prefixed records on stdin (as v11's CLI): PASS or FAIL for each; --allow-fail accepts
// status=FAIL records (schema and invariants only).
int main(int argc, char** argv) {
    const bool allow_fail = argc > 1 && std::string(argv[1]) == "--allow-fail";
    std::string length;
    while (std::getline(std::cin, length)) {
        unsigned count;
        try {
            count = std::stoul(length);
            if (count > 100000) return 2;
        } catch (...) {
            return 2;
        }
        std::string line(count, '\0');
        std::cin.read(line.data(), count);
        if (static_cast<unsigned>(std::cin.gcount()) != count) return 2;
        try {
            asterbench_v12::validate(line, allow_fail);
            std::cout << "PASS\n";
        } catch (const std::exception&) {
            std::cout << "FAIL\n";
        }
    }
}
